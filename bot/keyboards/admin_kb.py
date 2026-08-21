from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


ADMIN_MENU_BUTTONS = {
    "👤 Додати співробітника",
    "👥 Співробітники",
    "📋 Причини списання",
    "📜 Історія",
    "📊 Статистика",
}


def cancel_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Скасувати"))
    return builder.as_markup(resize_keyboard=True)


def admin_menu_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="👤 Додати співробітника"),
        KeyboardButton(text="👥 Співробітники"),
    )
    builder.row(
        KeyboardButton(text="📋 Причини списання"),
        KeyboardButton(text="📜 Історія"),
    )
    builder.row(
        KeyboardButton(text="📊 Статистика"),
        KeyboardButton(text="❓ Допомога"),
    )
    return builder.as_markup(resize_keyboard=True)


HISTORY_PAGE_SIZE = 5


def history_page_kb(offset: int, total: int) -> InlineKeyboardMarkup | None:
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(
            text="⬅️ Новіші",
            callback_data=f"history_page_{max(0, offset - HISTORY_PAGE_SIZE)}",
        ))
    if offset + HISTORY_PAGE_SIZE < total:
        remaining = total - offset - HISTORY_PAGE_SIZE
        nav.append(InlineKeyboardButton(
            text=f"Старіші ➡️ ({remaining})",
            callback_data=f"history_page_{offset + HISTORY_PAGE_SIZE}",
        ))
    if not nav:
        return None
    builder = InlineKeyboardBuilder()
    builder.row(*nav)
    return builder.as_markup()


def stats_period_kb(period: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    week_label  = "✅ Тиждень" if period == "week"  else "📅 Тиждень"
    month_label = "✅ Місяць"  if period == "month" else "📅 Місяць"
    builder.button(text=week_label,  callback_data="stats_period_week")
    builder.button(text=month_label, callback_data="stats_period_month")
    builder.adjust(2)
    return builder.as_markup()


def staff_list_kb(staff_list: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for s in staff_list:
        status = "✅" if s.tg_id else "⏳"
        builder.row(
            InlineKeyboardButton(
                text=f"{status} {s.name}",
                callback_data="noop",
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=f"remove_staff_{s.id}",
            ),
        )
    return builder.as_markup()


def reasons_list_kb(reasons: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for r in reasons:
        builder.row(
            InlineKeyboardButton(text=r.name, callback_data="noop"),
            InlineKeyboardButton(
                text="❌",
                callback_data=f"remove_reason_{r.id}",
            ),
        )
    builder.row(
        InlineKeyboardButton(text="➕ Додати причину", callback_data="add_reason"),
        InlineKeyboardButton(text="📥 З Poster", callback_data="import_reasons"),
    )
    return builder.as_markup()


def confirm_delete_staff_kb(staff_id: int, has_pending: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_pending:
        builder.row(InlineKeyboardButton(
            text="⚠️ Видалити разом із запитами",
            callback_data=f"confirm_remove_staff_{staff_id}",
        ))
    else:
        builder.row(InlineKeyboardButton(
            text="✅ Так, видалити",
            callback_data=f"confirm_remove_staff_{staff_id}",
        ))
    builder.row(InlineKeyboardButton(text="Скасувати", callback_data="noop_close"))
    return builder.as_markup()


def confirm_reject_kb(draft_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Списати", callback_data=f"confirm_{draft_id}")
    builder.button(text="❌ Відхилити", callback_data=f"reject_{draft_id}")
    builder.adjust(2)
    return builder.as_markup()


def confirm_disconnect_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Так, відключити", callback_data="disconnect_confirm"))
    builder.row(InlineKeyboardButton(text="Скасувати", callback_data="noop_close"))
    return builder.as_markup()


def connect_poster_kb(oauth_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔌 Підключити Poster", url=oauth_url)
    return builder.as_markup()


def onboarding_role_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏪 Я адміністратор закладу", callback_data="onboard_admin"))
    builder.row(InlineKeyboardButton(text="👤 У мене є код запрошення", callback_data="onboard_staff_code"))
    return builder.as_markup()
