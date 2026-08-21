import traceback

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import ErrorEvent, Message

from core.config import settings
from db.repositories.clients import ClientRepo
from db.session import AsyncSessionLocal

router = Router()


def _is_super_admin(tg_id: int) -> bool:
    return settings.super_admin_chat_id is not None and tg_id == settings.super_admin_chat_id


# ──────────────────────────────────────────────
# /admin — панель супер-адміна
# ──────────────────────────────────────────────

@router.message(
    Command("admin"),
    lambda m: m.from_user and _is_super_admin(m.from_user.id),
)
async def cmd_super_admin(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        overview = await ClientRepo(session).get_all_clients_overview()

    if not overview:
        await message.answer("🔧 Немає підключених закладів.")
        return

    total_staff = sum(o["staff_count"] for o in overview)
    total_confirmed = sum(o["confirmed_count"] for o in overview)
    total_pending = sum(o["pending_count"] for o in overview)

    lines = [
        "🔧 <b>Super Admin Panel</b>\n",
        f"🏪 Закладів: <b>{len(overview)}</b>",
        f"🧑‍💼 Співробітників: <b>{total_staff}</b>",
        f"✅ Списань всього: <b>{total_confirmed}</b>",
        (f"⏳ Очікують: <b>{total_pending}</b>" if total_pending else ""),
    ]

    for o in overview:
        client = o["client"]
        date_str = client.created_at.strftime("%d.%m.%Y") if client.created_at else "—"
        admin_strs = []
        for a in o["admins"]:
            admin_strs.append(f"<code>{a.tg_id}</code>")

        lines.append(f"\n━━━━━━━━━━━━━━━")
        lines.append(f"🏪 <b>{client.account_name}</b>  <i>(з {date_str})</i>")
        lines.append(f"👤 Адміни: {', '.join(admin_strs) or '—'}")
        lines.append(
            f"🧑‍💼 Стафф: {o['staff_count']}  "
            f"✅ Списань: {o['confirmed_count']}"
            + (f"  ⏳ Pending: {o['pending_count']}" if o["pending_count"] else "")
        )

    await message.answer("\n".join(l for l in lines if l))


# ──────────────────────────────────────────────
# Глобальний перехоплювач помилок
# ──────────────────────────────────────────────

@router.errors()
async def global_error_handler(event: ErrorEvent) -> bool:
    exc = event.exception
    update = event.update

    # Short traceback — last 3 lines
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_short = "".join(tb_lines[-3:]).strip()

    # Who triggered it
    user_info = "невідомий"
    if update.message and update.message.from_user:
        u = update.message.from_user
        user_info = f"{u.id}" + (f" (@{u.username})" if u.username else "")
    elif update.callback_query and update.callback_query.from_user:
        u = update.callback_query.from_user
        user_info = f"{u.id}" + (f" (@{u.username})" if u.username else "")

    event_type = "message" if update.message else "callback" if update.callback_query else "інше"

    text = (
        f"🚨 <b>Помилка в боті</b>\n\n"
        f"👤 Користувач: {user_info}\n"
        f"🔧 Подія: {event_type}\n\n"
        f"❌ <code>{type(exc).__name__}: {exc}</code>\n\n"
        f"<pre>{tb_short[:800]}</pre>"
    )

    if settings.super_admin_chat_id:
        try:
            from aiogram import Bot
            # Re-use the bot instance from the update context
            bot: Bot = event.update.bot  # type: ignore[attr-defined]
            await bot.send_message(settings.super_admin_chat_id, text)
        except Exception:
            pass  # don't loop on errors in error handler

    return True  # mark as handled so aiogram doesn't log it twice
