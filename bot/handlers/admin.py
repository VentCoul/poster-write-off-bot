from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select as sa_select

from bot.handlers.oauth import ask_poster_account
from bot.keyboards.admin_kb import (
    ADMIN_MENU_BUTTONS,
    HISTORY_PAGE_SIZE,
    admin_menu_kb,
    cancel_kb,
    confirm_delete_staff_kb,
    confirm_disconnect_kb,
    confirm_reject_kb,
    history_page_kb,
    reasons_list_kb,
    staff_list_kb,
    stats_period_kb,
)
from bot.states import AdminStates
from db.models import Staff as StaffModel
from db.repositories.clients import ClientRepo
from db.repositories.reasons import ReasonRepo
from db.repositories.staff import StaffRepo
from db.repositories.writeoffs import WriteoffRepo
from db.session import AsyncSessionLocal
from services.poster import PosterClient, PosterError

router = Router()


def _fmt(amount) -> str:
    return f"{float(amount):.3f}".rstrip("0").rstrip(".")


class IsAdminFilter(BaseFilter):
    """Passes only if middleware injected is_admin=True (config or DB admin).

    aiogram always calls filters with the raw event as the first positional
    argument. `event` here just absorbs that slot — without it, aiogram would
    bind the event itself to `is_admin` (truthy for any Message/CallbackQuery),
    making this filter pass for *everyone* regardless of admin status.
    """
    async def __call__(self, event: TelegramObject, is_admin: bool = False) -> bool:
        return is_admin


# ──────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────

@router.message(Command("start"), IsAdminFilter())
async def admin_start(message: Message, state: FSMContext) -> None:
    await state.clear()  # drop any stale onboarding state from a previous, abandoned /start

    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(message.from_user.id)

    if client:
        await message.answer(
            f"✅ Підключено до <b>{client.account_name}</b>\n\n"
            "Використовуйте кнопки нижче для керування:",
            reply_markup=admin_menu_kb(),
        )
    else:
        await _send_oauth_link(message, state)


# ──────────────────────────────────────────────
# OAuth helpers
# ──────────────────────────────────────────────

async def _send_oauth_link(message: Message, state: FSMContext) -> None:
    await message.answer("👋 Вітаємо!\n\nДля початку роботи підключіть ваш Poster акаунт.")
    await ask_poster_account(message, state)


# ──────────────────────────────────────────────
# /disconnect — switch to a different Poster account
# ──────────────────────────────────────────────

@router.message(Command("disconnect"), IsAdminFilter())
async def cmd_disconnect(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(message.from_user.id)

    if not client:
        await message.answer("ℹ️ У вас і так немає підключеного Poster акаунту.")
        return

    await message.answer(
        f"⚠️ Відключити акаунт <b>{client.account_name}</b>?\n\n"
        "Після цього ви зможете підключити інший заклад через /start. "
        "Дані вашого закладу (співробітники, історія списань) нікуди не зникнуть — "
        "просто оберіть /start знову, щоб під'єднати той самий акаунт назад.",
        reply_markup=confirm_disconnect_kb(),
    )


@router.callback_query(F.data == "disconnect_confirm", IsAdminFilter())
async def cb_disconnect_confirm(call: CallbackQuery) -> None:
    async with AsyncSessionLocal() as session:
        client_repo = ClientRepo(session)
        client = await client_repo.get_by_admin_tg_id(call.from_user.id)
        if not client:
            await call.message.edit_text("ℹ️ У вас і так немає підключеного Poster акаунту.")
            await call.answer()
            return
        await client_repo.remove_admin(call.from_user.id)
        await session.commit()

    await call.message.edit_text(
        f"✅ Акаунт <b>{client.account_name}</b> відключено.\n\n"
        "Відправте /start, щоб підключити інший заклад."
    )
    await call.answer()


async def _cancel_fsm(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано.", reply_markup=admin_menu_kb())


# ──────────────────────────────────────────────
# Додати співробітника
# ──────────────────────────────────────────────

async def _start_add_staff(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminStates.entering_staff_name)
    await message.answer("👤 Введіть ім'я та прізвище співробітника:", reply_markup=cancel_kb())


@router.message(Command("add_staff"), IsAdminFilter())
async def cmd_add_staff(message: Message, state: FSMContext) -> None:
    await _start_add_staff(message, state)


@router.message(F.text == "👤 Додати співробітника", IsAdminFilter())
async def btn_add_staff(message: Message, state: FSMContext) -> None:
    await _start_add_staff(message, state)


@router.message(AdminStates.entering_staff_name)
async def handle_staff_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()

    if name == "❌ Скасувати" or name in ADMIN_MENU_BUTTONS:
        await _cancel_fsm(message, state)
        return

    await state.clear()
    if not name:
        await message.answer("❌ Ім'я не може бути порожнім.", reply_markup=admin_menu_kb())
        return

    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(message.from_user.id)
        if not client:
            await message.answer("❌ Спочатку підключіть Poster акаунт.")
            return
        staff = await StaffRepo(session).create(client.id, name)
        await session.commit()

    bot_username = (await message.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={staff.invite_token}"
    await message.answer(
        f"✅ Запрошення для <b>{name}</b> створено!\n\n"
        f"Посилання для реєстрації:\n{link}",
    )


# ──────────────────────────────────────────────
# Список співробітників
# ──────────────────────────────────────────────

async def _show_staff(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(message.from_user.id)
        if not client:
            await message.answer("❌ Poster не підключено.")
            return
        staff_list = await StaffRepo(session).list_by_client(client.id)

    if not staff_list:
        await message.answer("👥 Поки немає зареєстрованих співробітників.")
        return

    await message.answer(
        "👥 <b>Співробітники:</b>\n✅ — зареєстрований  ⏳ — очікує реєстрації\n\n"
        "Натисніть ❌ поруч із ім'ям щоб видалити:",
        reply_markup=staff_list_kb(staff_list),
    )


@router.message(Command("staff"), IsAdminFilter())
async def cmd_staff(message: Message) -> None:
    await _show_staff(message)


@router.message(F.text == "👥 Співробітники", IsAdminFilter())
async def btn_staff(message: Message) -> None:
    await _show_staff(message)


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data.startswith("remove_staff_"), IsAdminFilter())
async def cb_remove_staff(call: CallbackQuery) -> None:
    staff_id = int(call.data.removeprefix("remove_staff_"))

    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(call.from_user.id)
        if not client:
            await call.answer("❌ Помилка", show_alert=True)
            return

        stmt = sa_select(StaffModel).where(
            StaffModel.id == staff_id,
            StaffModel.client_id == client.id,
        )
        staff = (await session.execute(stmt)).scalar_one_or_none()
        if not staff:
            await call.answer("❌ Співробітника не знайдено", show_alert=True)
            return

        pending_count = 0
        if staff.tg_id:
            pending_count = await WriteoffRepo(session).count_pending_by_staff_tg_id(staff.tg_id)

    if pending_count > 0:
        text = (
            f"⚠️ У <b>{staff.name}</b> є <b>{pending_count}</b> незакритих запит(ів) на списання.\n\n"
            "Якщо видалити співробітника — ці запити залишаться без виконавця. Продовжити?"
        )
    else:
        text = f"Видалити співробітника <b>{staff.name}</b>?"

    await call.message.answer(text, reply_markup=confirm_delete_staff_kb(staff_id, pending_count > 0))
    await call.answer()


@router.callback_query(F.data == "noop_close")
async def cb_noop_close(call: CallbackQuery) -> None:
    await call.message.delete()
    await call.answer()


@router.callback_query(F.data.startswith("confirm_remove_staff_"), IsAdminFilter())
async def cb_confirm_remove_staff(call: CallbackQuery) -> None:
    staff_id = int(call.data.removeprefix("confirm_remove_staff_"))

    async with AsyncSessionLocal() as session:
        staff_repo = StaffRepo(session)
        client = await ClientRepo(session).get_by_admin_tg_id(call.from_user.id)
        if not client:
            await call.answer("❌ Помилка", show_alert=True)
            return

        stmt = sa_select(StaffModel).where(
            StaffModel.id == staff_id,
            StaffModel.client_id == client.id,
        )
        staff = (await session.execute(stmt)).scalar_one_or_none()
        if not staff:
            await call.message.edit_text("❌ Співробітника вже видалено.")
            await call.answer()
            return

        name = staff.name
        await staff_repo.delete(staff)
        await session.commit()
        staff_list = await staff_repo.list_by_client(client.id)

    await call.message.edit_text(f"✅ {name} видалено.")
    await call.answer()

    if not staff_list:
        await call.bot.send_message(call.from_user.id, "👥 Поки немає зареєстрованих співробітників.")
    else:
        await call.bot.send_message(
            call.from_user.id,
            "👥 <b>Співробітники:</b>\n✅ — зареєстрований  ⏳ — очікує реєстрації\n\nНатисніть ❌ поруч із ім'ям щоб видалити:",
            reply_markup=staff_list_kb(staff_list),
        )


# ──────────────────────────────────────────────
# Причини списання
# ──────────────────────────────────────────────

async def _show_reasons(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(message.from_user.id)
        if not client:
            await message.answer("❌ Poster не підключено.")
            return
        reasons = await ReasonRepo(session).list_by_client(client.id)

    if not reasons:
        await message.answer(
            "📋 Причин списання ще немає.\n\nДодайте першу:",
            reply_markup=reasons_list_kb([]),
        )
    else:
        await message.answer(
            "📋 <b>Причини списання:</b>\n\nНатисніть ❌ щоб видалити:",
            reply_markup=reasons_list_kb(reasons),
        )


@router.message(Command("reasons"), IsAdminFilter())
async def cmd_reasons(message: Message) -> None:
    await _show_reasons(message)


@router.message(F.text == "📋 Причини списання", IsAdminFilter())
async def btn_reasons(message: Message) -> None:
    await _show_reasons(message)


@router.callback_query(F.data == "add_reason", IsAdminFilter())
async def cb_add_reason_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.entering_reason_name)
    await call.message.answer("📝 Введіть назву причини списання:", reply_markup=cancel_kb())
    await call.answer()


@router.message(AdminStates.entering_reason_name)
async def handle_reason_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if name == "❌ Скасувати" or name in ADMIN_MENU_BUTTONS:
        await _cancel_fsm(message, state)
        return
    if not name:
        await message.answer("❌ Назва не може бути порожньою.")
        return
    await state.update_data(reason_name=name)
    await state.set_state(AdminStates.entering_reason_poster_id)
    await message.answer(
        f"Назва: <b>{name}</b>\n\n"
        "Тепер введіть Poster ID причини (число).\n"
        "Знайти його можна в налаштуваннях Poster → Причини списання."
    )


@router.message(AdminStates.entering_reason_poster_id)
async def handle_reason_poster_id(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text == "❌ Скасувати" or text in ADMIN_MENU_BUTTONS:
        await _cancel_fsm(message, state)
        return
    try:
        poster_id = int(text)
    except ValueError:
        await message.answer("❌ Poster ID має бути числом. Спробуйте ще раз:")
        return

    data = await state.get_data()
    name = data["reason_name"]
    await state.clear()

    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(message.from_user.id)
        if not client:
            await message.answer("❌ Poster не підключено.")
            return
        await ReasonRepo(session).add(client.id, name, poster_id)
        await session.commit()
        reasons = await ReasonRepo(session).list_by_client(client.id)

    await message.answer(
        f"✅ Причину <b>{name}</b> (Poster ID: {poster_id}) додано.",
        reply_markup=reasons_list_kb(reasons),
    )


@router.callback_query(F.data.startswith("remove_reason_"), IsAdminFilter())
async def cb_remove_reason(call: CallbackQuery) -> None:
    reason_id = int(call.data.removeprefix("remove_reason_"))

    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(call.from_user.id)
        if not client:
            await call.answer("❌ Помилка", show_alert=True)
            return
        reason_repo = ReasonRepo(session)
        removed = await reason_repo.remove_by_id(client.id, reason_id)
        await session.commit()
        reasons = await reason_repo.list_by_client(client.id)

    if not removed:
        await call.answer("❌ Причину не знайдено", show_alert=True)
        return

    await call.answer("✅ Видалено")
    await call.message.edit_reply_markup(reply_markup=reasons_list_kb(reasons))


@router.callback_query(F.data == "import_reasons", IsAdminFilter())
async def cb_import_reasons(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer("⏳ Завантажую причини з Poster...")

    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(call.from_user.id)
        if not client:
            await call.message.answer("❌ Poster не підключено.")
            return

        poster = PosterClient(client.access_token)
        raw_reasons = await poster.get_writeoff_reasons()
        await poster.aclose()

        if not raw_reasons:
            await call.message.answer(
                "😕 Poster не повернув жодної причини.\n\n"
                "Переконайтесь що в Poster налаштовані причини списань "
                "(Налаштування → Склад → Причини списання)."
            )
            return

        repo = ReasonRepo(session)
        added, skipped = 0, 0
        for r in raw_reasons:
            # Poster returns: {reason_id, reason_name} or {id, name}
            rid = r.get("reason_id") or r.get("id")
            rname = r.get("reason_name") or r.get("name")
            if not rid or not rname:
                continue
            existing = await repo.get_by_name(client.id, rname)
            if existing:
                skipped += 1
            else:
                await repo.add(client.id, rname, int(rid))
                added += 1

        await session.commit()
        reasons = await repo.list_by_client(client.id)

    parts = []
    if added:
        parts.append(f"✅ Додано нових: <b>{added}</b>")
    if skipped:
        parts.append(f"⏭ Вже існують: <b>{skipped}</b>")
    summary = "\n".join(parts) if parts else "Нічого не імпортовано."

    await call.message.answer(
        f"📥 <b>Імпорт завершено</b>\n\n{summary}\n\n"
        "📋 <b>Актуальний список причин:</b>\n\nНатисніть ❌ щоб видалити:",
        reply_markup=reasons_list_kb(reasons),
    )


# ──────────────────────────────────────────────
# Статистика
# ──────────────────────────────────────────────

def _build_stats_text(stats: dict, period: str) -> str:
    period_label = "тиждень" if period == "week" else "місяць"
    lines = [f"📊 <b>Статистика за {period_label}</b>\n"]

    total = stats["confirmed"] + stats["rejected"] + stats["pending"]
    lines.append(f"Всього запитів: <b>{total}</b>")
    lines.append(f"✅ Підтверджено: <b>{stats['confirmed']}</b>")
    lines.append(f"❌ Відхилено: <b>{stats['rejected']}</b>")
    if stats["pending"]:
        lines.append(f"⏳ Очікують: <b>{stats['pending']}</b>")

    if stats["by_staff"]:
        lines.append("\n👥 <b>По співробітниках (підтверджені):</b>")
        for name, cnt in stats["by_staff"]:
            lines.append(f"• {name} — {cnt} спис.")

    if stats["by_reason"]:
        lines.append("\n📋 <b>По причинах:</b>")
        for reason, cnt in stats["by_reason"]:
            lines.append(f"• {reason} — {cnt}")

    if total == 0:
        lines.append("\nЗа цей період списань не було.")

    return "\n".join(lines)


async def _show_stats(event, period: str) -> None:
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(days=7 if period == "week" else 30)

    is_message = isinstance(event, Message)
    tg_id = event.from_user.id

    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(tg_id)
        if not client:
            target = event if is_message else event.message
            await target.answer("❌ Poster не підключено.")
            return
        stats = await WriteoffRepo(session).get_stats(client.id, since)

    text = _build_stats_text(stats, period)
    kb = stats_period_kb(period)

    if is_message:
        await event.answer(text, reply_markup=kb)
    else:
        await event.message.edit_text(text, reply_markup=kb)


@router.message(F.text == "📊 Статистика", IsAdminFilter())
async def btn_stats(message: Message) -> None:
    await _show_stats(message, period="week")


@router.callback_query(F.data.startswith("stats_period_"), IsAdminFilter())
async def cb_stats_period(call: CallbackQuery) -> None:
    period = call.data.removeprefix("stats_period_")
    await _show_stats(call, period)
    await call.answer()


# ──────────────────────────────────────────────
# Допомога
# ──────────────────────────────────────────────

ADMIN_HELP_TEXT = """❓ <b>Допомога — Адміністратор</b>

<b>Кнопки меню:</b>
👤 <b>Додати співробітника</b> — створює запрошення. Надішліть посилання співробітнику, він перейде і зареєструється.
👥 <b>Співробітники</b> — список усіх співробітників. ✅ — зареєстрований, ⏳ — очікує реєстрації. Кнопка ❌ — видалити.
📋 <b>Причини списання</b> — перелік причин які бачать співробітники при відправці. Додайте назву і Poster ID причини (з налаштувань Poster).
📜 <b>Історія</b> — останні підтверджені списання.

<b>Запити на списання:</b>
Коли співробітник відправляє запит — ви отримуєте повідомлення з кнопками:
✅ <b>Списати</b> — підтверджує і створює списання в Poster
❌ <b>Відхилити</b> — запитує причину відхилення і сповіщає співробітника

<b>Підключення Poster:</b>
Якщо потрібно підключити інший заклад — відправте /disconnect, підтвердіть відключення поточного акаунту, а тоді /start для підключення нового."""


@router.message(F.text == "❓ Допомога", IsAdminFilter())
async def btn_help_admin(message: Message) -> None:
    await message.answer(ADMIN_HELP_TEXT, reply_markup=admin_menu_kb())


# ──────────────────────────────────────────────
# Історія
# ──────────────────────────────────────────────

def _fmt_history(drafts: list, offset: int, total: int) -> str:
    lines = [f"📋 <b>Списання ({offset + 1}–{offset + len(drafts)} з {total}):</b>\n"]
    for d in drafts:
        date_str = d.created_at.strftime("%d.%m %H:%M")
        items_str = ", ".join(f"{i.ingredient_name} {_fmt(i.amount)}{i.unit}" for i in d.items)
        lines.append(f"<b>{d.staff_name}</b> · {date_str}\n  {items_str}")
    return "\n\n".join(lines)


async def _show_history(event, offset: int = 0) -> None:
    is_message = isinstance(event, Message)
    tg_id = event.from_user.id

    async with AsyncSessionLocal() as session:
        client = await ClientRepo(session).get_by_admin_tg_id(tg_id)
        if not client:
            target = event if is_message else event.message
            await target.answer("❌ Poster не підключено.")
            return
        total = await WriteoffRepo(session).count_confirmed(client.id)
        drafts = await WriteoffRepo(session).get_recent_logs(client.id, limit=HISTORY_PAGE_SIZE, offset=offset)

    if not drafts:
        target = event if is_message else event.message
        await target.answer("📋 Списань ще не було.")
        return

    text = _fmt_history(drafts, offset, total)
    kb = history_page_kb(offset, total)

    if is_message:
        await event.answer(text, reply_markup=kb)
    else:
        await event.message.edit_text(text, reply_markup=kb)


@router.message(Command("history"), IsAdminFilter())
async def cmd_history(message: Message) -> None:
    await _show_history(message)


@router.message(F.text == "📜 Історія", IsAdminFilter())
async def btn_history(message: Message) -> None:
    await _show_history(message)


@router.callback_query(F.data.startswith("history_page_"), IsAdminFilter())
async def cb_history_page(call: CallbackQuery) -> None:
    offset = int(call.data.removeprefix("history_page_"))
    await _show_history(call, offset=offset)
    await call.answer()


# ──────────────────────────────────────────────
# Підтвердження / відхилення списань
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("confirm_"), IsAdminFilter())
async def cb_confirm(call: CallbackQuery) -> None:
    draft_id = int(call.data.removeprefix("confirm_"))

    async with AsyncSessionLocal() as session:
        writeoff_repo = WriteoffRepo(session)
        client_repo = ClientRepo(session)

        draft = await writeoff_repo.get_with_items(draft_id)
        if not draft:
            await call.answer("❌ Запит не знайдено", show_alert=True)
            return
        if draft.status != "pending":
            await call.answer("ℹ️ Цей запит вже оброблено", show_alert=True)
            return

        from db.models import Client
        client = await session.get(Client, draft.client_id)
        if not client:
            await call.answer("❌ Акаунт Poster не знайдено", show_alert=True)
            return

        poster = PosterClient(client.access_token)

        if draft.storage_id:
            storage_id = draft.storage_id
        else:
            storages = await poster.get_storages()
            storage_id = int(storages[0]["storage_id"]) if storages else 1

        ingredients = [
            {
                "ingredient_id": item.ingredient_id,
                "amount": float(item.amount),
                "item_type": item.item_type,
            }
            for item in draft.items
        ]

        poster_id = None
        reason = draft.comment or f"Telegram: {draft.staff_name}"
        try:
            poster_id = await poster.create_writeoff(
                storage_id, ingredients,
                reason=reason,
                reason_id=draft.poster_reason_id,
            )
        except PosterError as e:
            await call.message.edit_text(f"❌ Помилка Poster API: {e}\n\nСписання не збережено.")
            await poster.aclose()
            return
        finally:
            await poster.aclose()

        await writeoff_repo.confirm(draft, call.from_user.id, poster_id)
        await session.commit()

    lines = [f"• {i.ingredient_name} — {_fmt(i.amount)} {i.unit}" for i in draft.items]
    await call.message.edit_text(
        f"✅ <b>Списання #{poster_id or draft_id} підтверджено</b>\n\n" + "\n".join(lines),
    )

    try:
        await call.bot.send_message(
            draft.staff_tg_id,
            "✅ Ваш запит на списання підтверджено адміністратором.",
        )
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("reject_"), IsAdminFilter())
async def cb_reject_start(call: CallbackQuery, state: FSMContext) -> None:
    draft_id = int(call.data.removeprefix("reject_"))
    await state.update_data(reject_draft_id=draft_id)
    await state.set_state(AdminStates.entering_reject_reason)
    await call.message.answer("✏️ Введіть причину відхилення:")
    await call.answer()


@router.message(AdminStates.entering_reject_reason)
async def handle_reject_reason(message: Message, state: FSMContext) -> None:
    reason = message.text.strip()
    data = await state.get_data()
    draft_id = data.get("reject_draft_id")
    await state.clear()

    async with AsyncSessionLocal() as session:
        writeoff_repo = WriteoffRepo(session)
        draft = await writeoff_repo.get_with_items(draft_id)
        if not draft or draft.status != "pending":
            await message.answer("ℹ️ Запит вже оброблено або не існує.")
            return
        await writeoff_repo.reject(draft, reason)
        await session.commit()

    await message.answer("✅ Запит відхилено.")
    try:
        from bot.keyboards.staff_kb import resubmit_kb
        items_str = "\n".join(
            f"• {i.ingredient_name} — {_fmt(i.amount)} {i.unit}" for i in draft.items
        )
        await message.bot.send_message(
            draft.staff_tg_id,
            f"❌ Ваш запит на списання відхилено.\n\n"
            f"<b>Причина:</b> {reason}\n\n"
            f"<b>Товари з запиту:</b>\n{items_str}",
            reply_markup=resubmit_kb(draft_id),
        )
    except Exception:
        pass
