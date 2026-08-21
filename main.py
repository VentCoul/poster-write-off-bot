import asyncio
import logging
import os
from datetime import datetime, timedelta

import structlog
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.handlers import admin, oauth, staff, super_admin
from bot.middlewares.auth import StaffAuthMiddleware
from core.config import settings
from db.models import Base
from db.repositories.clients import ClientRepo
from db.repositories.writeoffs import WriteoffRepo
from db.session import AsyncSessionLocal, engine
from web.routes import app as web_app

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
logging.basicConfig(level=logging.INFO)
log = structlog.get_logger()


async def create_tables() -> None:
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        migrations = [
            ("writeoff_items",  "item_type",        "VARCHAR NOT NULL DEFAULT '4'"),
            ("writeoff_drafts", "poster_reason_id",  "INTEGER"),
            ("writeoff_drafts", "reason_name",       "VARCHAR"),
            ("writeoff_drafts", "storage_id",        "INTEGER"),
            ("writeoff_drafts", "comment",           "VARCHAR"),
            ("writeoff_drafts", "reminded_at",       "DATETIME"),
        ]
        for table, column, col_def in migrations:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
                log.info("db.migration", table=table, column=column)
            except Exception:
                pass  # column already exists


REMIND_AFTER_HOURS = 2      # нагадати якщо запит висить > 2 годин
REMIND_CHECK_INTERVAL = 30 * 60  # перевіряти кожні 30 хвилин

CATALOG_REFRESH_INTERVAL = 15 * 60  # оновлювати каталог кожні 15 хвилин


async def catalog_refresh_job() -> None:
    """Keep every client's catalog fresh regardless of whether anyone searches.

    Before this existed the catalog was only reloaded lazily — inside the search
    handler, and only once its 1h TTL had expired. Editing a dish in Poster meant
    waiting up to an hour before staff could find it. Logs showed real intervals
    of 19h between reloads on quiet nights.
    """
    from services.matcher import CatalogRefreshError, refresh_catalog

    try:
        async with AsyncSessionLocal() as session:
            clients = await ClientRepo(session).get_active()
        for client in clients:
            try:
                count = await refresh_catalog(client.id, client.access_token)
                log.info("catalog.refreshed", client_id=client.id, count=count)
            except CatalogRefreshError:
                pass  # already logged; previous catalog kept
    except Exception as e:
        log.warning("catalog_refresh_job.error", error=str(e))


async def reminder_job(bot: Bot) -> None:
    """Background task: remind admins about pending drafts older than REMIND_AFTER_HOURS."""
    from bot.keyboards.admin_kb import confirm_reject_kb
    try:
        threshold = datetime.utcnow() - timedelta(hours=REMIND_AFTER_HOURS)
        async with AsyncSessionLocal() as session:
            repo = WriteoffRepo(session)
            drafts = await repo.get_unreminded_pending(threshold)
            for draft in drafts:
                admin_ids = await ClientRepo(session).get_admin_tg_ids(draft.client_id)
                age_h = int((datetime.utcnow() - draft.created_at).total_seconds() // 3600)
                items_str = "\n".join(
                    f"• {i.ingredient_name} — {float(i.amount):.3f}".rstrip("0").rstrip(".") + f" {i.unit}"
                    for i in draft.items
                ) if draft.items else ""
                text = (
                    f"⏰ <b>Нагадування!</b>\n\n"
                    f"Запит від <b>{draft.staff_name}</b> чекає підтвердження вже <b>{age_h} год.</b>\n\n"
                    + (items_str + "\n\n" if items_str else "")
                    + "Підтвердіть або відхиліть:"
                )
                for admin_id in admin_ids:
                    try:
                        await bot.send_message(admin_id, text, reply_markup=confirm_reject_kb(draft.id))
                    except Exception:
                        pass
                await repo.mark_reminded(draft)
            if drafts:
                await session.commit()
                log.info("reminder.sent", count=len(drafts))
    except Exception as e:
        log.warning("reminder_job.error", error=str(e))


async def run_bot() -> None:
    bot = Bot(
        token=settings.telegram_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    # Outer middleware: aiogram checks a handler's filters (incl. IsAdminFilter)
    # BEFORE inner middlewares run, so is_admin/staff must be resolved here to
    # actually influence routing — an inner middleware only wraps the handler
    # call *after* a match was already decided.
    dp.message.outer_middleware(StaffAuthMiddleware())
    dp.callback_query.outer_middleware(StaffAuthMiddleware())

    dp.include_router(super_admin.router)  # першим — щоб error handler діяв глобально
    dp.include_router(oauth.router)
    dp.include_router(admin.router)
    dp.include_router(staff.router)

    log.info("bot.starting")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminder_job, "interval", seconds=REMIND_CHECK_INTERVAL, args=[bot])
    scheduler.add_job(catalog_refresh_job, "interval", seconds=CATALOG_REFRESH_INTERVAL)
    scheduler.start()
    await dp.start_polling(bot, skip_updates=True)


async def run_web() -> None:
    config = uvicorn.Config(web_app, host="0.0.0.0", port=settings.web_port, log_level="warning")
    server = uvicorn.Server(config)
    log.info("web.starting", port=settings.web_port)
    await server.serve()


async def main() -> None:
    os.makedirs("data", exist_ok=True)
    await create_tables()
    await asyncio.gather(run_bot(), run_web())


if __name__ == "__main__":
    asyncio.run(main())
