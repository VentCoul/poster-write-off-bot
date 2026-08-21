from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

PAGE_SIZE = 5  # re-export for handlers


STAFF_HISTORY_PAGE_SIZE = 5


def staff_menu_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🔍 Додати товар"),
        KeyboardButton(text="🛒 Кошик"),
    )
    builder.row(
        KeyboardButton(text="📤 Надіслати на підтвердження"),
    )
    builder.row(
        KeyboardButton(text="📜 Історія"),
        KeyboardButton(text="❓ Допомога"),
    )
    return builder.as_markup(resize_keyboard=True)


# Keep for legacy callers — maps to staff_menu_kb
def main_menu_kb(basket_count: int = 0) -> ReplyKeyboardMarkup:
    return staff_menu_kb()


# Poster write-off item types → short label shown in the picker so staff can tell
# a storage ingredient apart from a menu product/dish (esp. same-named entries).
_TYPE_TAG = {
    "4": "🥕 інгр.",
    "1": "📦 товар",
    "2": "🍽 страва",
    "5": "🧩 моди.",
}
SEARCH_LEGEND = "🥕 інгредієнт · 📦 товар · 🍽 страва"


def _type_tag(item_type) -> str:
    return _TYPE_TAG.get(str(item_type), "•")


def search_results_kb(results: list[dict], page: int = 0, page_size: int = PAGE_SIZE) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(results)
    start = page * page_size
    end = min(start + page_size, total)
    page_items = results[start:end]

    for item in page_items:
        tag = _type_tag(item.get("item_type", "4"))
        label = f"{tag} · {item['ingredient_name']}"
        unit = item.get("unit", "")
        if unit:
            label += f" ({unit})"
        builder.button(text=label, callback_data=f"select_{item['ingredient_id']}_{item.get('item_type','4')}")
    builder.adjust(1)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"search_page_{page - 1}")
        )
    if end < total:
        remaining = total - end
        nav_buttons.append(
            InlineKeyboardButton(text=f"➡️ Ще {remaining}", callback_data=f"search_page_{page + 1}")
        )
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text="❌ Скасувати пошук", callback_data="cancel_search"))
    return builder.as_markup()


def _fmt_amount(amount) -> str:
    return f"{float(amount):.3f}".rstrip("0").rstrip(".")


def basket_kb(items: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        iid = item["ingredient_id"]
        amt = _fmt_amount(item["amount"])
        unit = item.get("unit", "")
        builder.row(
            InlineKeyboardButton(
                text=f"✏️ {item['name']} — {amt} {unit}",
                callback_data=f"edit_qty_{iid}",
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=f"remove_{iid}",
            ),
        )
    builder.row(InlineKeyboardButton(text="🗑 Очистити кошик", callback_data="clear_basket"))
    return builder.as_markup()


def clear_basket_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, очистити", callback_data="clear_basket_confirm")
    builder.button(text="↩️ Назад", callback_data="show_basket")
    builder.adjust(2)
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати", callback_data="cancel_search")
    return builder.as_markup()


def storages_kb(storages: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for s in storages:
        builder.button(
            text=s["storage_name"],
            callback_data=f"storage_{s['storage_id']}",
        )
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_send"))
    return builder.as_markup()


def skip_comment_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➡️ Пропустити", callback_data="comment_skip")
    builder.button(text="❌ Скасувати", callback_data="cancel_send")
    builder.adjust(2)
    return builder.as_markup()


def reasons_kb(reasons: list) -> InlineKeyboardMarkup:
    """reasons: list of WriteoffReason objects"""
    builder = InlineKeyboardBuilder()
    for r in reasons:
        builder.button(text=r.name, callback_data=f"reason_{r.id}_{r.poster_reason_id}")
    builder.button(text="➡️ Без причини", callback_data="reason_none")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_send"))
    return builder.as_markup()


def resubmit_kb(draft_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Повторити запит", callback_data=f"resubmit_{draft_id}")
    return builder.as_markup()


def staff_history_kb(drafts: list, offset: int, total: int) -> InlineKeyboardMarkup:
    """Inline keyboard for staff history: shows drafts with action buttons."""
    ps = STAFF_HISTORY_PAGE_SIZE
    status_emoji = {
        "pending":   "⏳",
        "confirmed": "✅",
        "rejected":  "❌",
        "cancelled": "🚫",
    }
    builder = InlineKeyboardBuilder()
    for draft in drafts:
        emoji = status_emoji.get(draft.status, "❓")
        date_str = draft.created_at.strftime("%d.%m")
        item_count = len(draft.items) if draft.items else 0
        label = f"{emoji} {date_str} — {item_count} поз."
        if draft.status == "pending":
            builder.row(
                InlineKeyboardButton(text=label, callback_data="noop"),
                InlineKeyboardButton(text="↩️ Скасувати", callback_data=f"cancel_draft_{draft.id}"),
            )
        elif draft.status == "rejected":
            builder.row(
                InlineKeyboardButton(text=label, callback_data="noop"),
                InlineKeyboardButton(text="🔄 Повторити", callback_data=f"resubmit_{draft.id}"),
            )
        else:
            builder.row(InlineKeyboardButton(text=label, callback_data="noop"))

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"staff_history_page_{offset - ps}"))
    page_num = offset // ps + 1
    page_total = max(1, (total + ps - 1) // ps)
    nav.append(InlineKeyboardButton(text=f"{page_num}/{page_total}", callback_data="noop"))
    if offset + ps < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"staff_history_page_{offset + ps}"))
    if nav:
        builder.row(*nav)
    return builder.as_markup()
