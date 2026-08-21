import re

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.oauth import ask_poster_account
from bot.keyboards.admin_kb import onboarding_role_kb
from bot.keyboards.staff_kb import basket_kb, cancel_kb, clear_basket_confirm_kb, main_menu_kb, reasons_kb, resubmit_kb, search_results_kb, skip_comment_kb, staff_history_kb, staff_menu_kb, storages_kb, PAGE_SIZE, STAFF_HISTORY_PAGE_SIZE, SEARCH_LEGEND, _type_tag
from bot.states import OnboardingStates, StaffStates
from db.models import Client, Staff
from db.repositories.clients import ClientRepo
from db.repositories.reasons import ReasonRepo
from db.repositories.staff import StaffRepo
from db.repositories.writeoffs import WriteoffRepo
from db.session import AsyncSessionLocal
from services.matcher import build_catalog, matcher, parse_leftovers, parse_modificators, parse_products
from services.poster import PosterClient

router = Router()

# Extracts the invite token whether the user pastes the bare code or a full
# https://t.me/<bot>?start=<token> link.
_START_PARAM_RE = re.compile(r"[?&]start=([A-Za-z0-9_-]+)")


async def _activate_by_invite_token(
    session: AsyncSession, tg_id: int, token: str
) -> tuple[Staff | None, str | None]:
    """Try to activate a staff invite. Returns (staff, error_message)."""
    staff_repo = StaffRepo(session)
    found = await staff_repo.get_by_invite_token(token)
    if not found:
        return None, "❌ Код запрошення недійсний або застарів. Попросіть адміна надіслати нове."
    if found.tg_id and found.tg_id != tg_id:
        return None, "❌ Це запрошення вже використане іншим користувачем."
    await staff_repo.activate(found, tg_id)
    await session.commit()
    return found, None


def _fmt(amount) -> str:
    """Format amount: remove trailing zeros, max 3 decimal places. 0.355→0.355, 1.0→1"""
    return f"{float(amount):.3f}".rstrip("0").rstrip(".")


async def _get_basket(state: FSMContext) -> list[dict]:
    data = await state.get_data()
    return data.get("basket", [])


async def _set_basket(state: FSMContext, basket: list[dict]) -> None:
    await state.update_data(basket=basket)


_ONBOARDING_STATE_NAMES = {
    OnboardingStates.entering_poster_account.state,
    OnboardingStates.entering_invite_code.state,
}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    # Drop stale onboarding state from a previous, abandoned /start — but keep
    # the basket, so a registered employee sending /start mid-shopping is fine.
    if await state.get_state() in _ONBOARDING_STATE_NAMES:
        await state.set_state(None)

    tg_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    invite_token = args[1].strip() if len(args) > 1 else None
    # "connected" is our own sentinel from the post-OAuth redirect, not an invite.
    if invite_token == "connected":
        invite_token = None

    async with AsyncSessionLocal() as session:
        staff_repo = StaffRepo(session)

        # Check if already registered (regardless of invite token)
        existing = await staff_repo.get_by_tg_id(tg_id)

        if invite_token and not existing:
            found, error = await _activate_by_invite_token(session, tg_id, invite_token)
            if error:
                await message.answer(error)
                return
            existing = found
            await message.answer(
                f"✅ Вас зареєстровано як <b>{existing.name}</b>!\n\n"
                "Тепер ви можете надсилати запити на списання."
            )
        elif not existing:
            # Unknown user with no (or a lost) invite payload — let them pick a path
            # explicitly instead of guessing from a bare /start.
            await message.answer(
                "👋 Вітаємо у Poster Write-off Bot!\n\n"
                "Оберіть, хто ви:",
                reply_markup=onboarding_role_kb(),
            )
            return

    basket = await _get_basket(state)
    await message.answer(
        f"👋 Привіт, <b>{existing.name}</b>!\n\n"
        "Використовуйте кнопки нижче для роботи зі списаннями.",
        reply_markup=staff_menu_kb(),
    )


# ──────────────────────────────────────────────
# Onboarding: role choice for unknown users
# ──────────────────────────────────────────────

@router.callback_query(F.data == "onboard_admin")
async def cb_onboard_admin(call: CallbackQuery, state: FSMContext) -> None:
    await ask_poster_account(call.message, state)
    await call.answer()


@router.callback_query(F.data == "onboard_staff_code")
async def cb_onboard_staff_code(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OnboardingStates.entering_invite_code)
    await call.message.answer(
        "👤 Вставте посилання-запрошення, яке надіслав адміністратор, "
        "або лише код після <code>?start=</code>:",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(OnboardingStates.entering_invite_code, lambda m: not (m.text or "").startswith("/"))
async def handle_invite_code(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == "❌ Скасувати":
        await state.clear()
        await message.answer("Скасовано.")
        return

    match = _START_PARAM_RE.search(text)
    token = match.group(1) if match else text
    if not token:
        await message.answer("❌ Не вдалося розпізнати код. Спробуйте ще раз або надішліть повне посилання.")
        return

    await state.clear()

    async with AsyncSessionLocal() as session:
        staff, error = await _activate_by_invite_token(session, message.from_user.id, token)

    if error:
        await message.answer(error)
        return

    await message.answer(
        f"✅ Вас зареєстровано як <b>{staff.name}</b>!\n\n"
        "Тепер ви можете надсилати запити на списання.",
        reply_markup=staff_menu_kb(),
    )


STAFF_MENU_TEXTS = {"🔍 Додати товар", "🛒 Кошик", "📤 Надіслати на підтвердження", "📜 Історія", "❓ Допомога"}


STAFF_HELP_TEXT = """❓ <b>Як користуватись ботом</b>

<b>Щоб створити запит на списання:</b>
1. Натисніть <b>🔍 Додати товар</b> і введіть назву
2. Оберіть товар зі списку, введіть кількість
3. Повторіть для всіх товарів
4. Натисніть <b>🛒 Кошик</b>, щоб переглянути
5. Натисніть <b>📤 Надіслати на підтвердження</b>
6. Оберіть склад (якщо є кілька), причину і додайте коментар за бажанням

Після відправки адміністратор отримає запит і підтвердить або відхилить його. Ви отримаєте сповіщення.

<b>Кнопки:</b>
🔍 Додати товар — пошук по каталогу
🛒 Кошик — перегляд та видалення позицій
📤 Надіслати — відправити запит адміністратору
❓ Допомога — ця підказка"""


@router.message(F.text == "❓ Допомога")
async def btn_help_staff(message: Message, state: FSMContext) -> None:
    await _safe_clear(state)
    await message.answer(STAFF_HELP_TEXT, reply_markup=staff_menu_kb())


@router.message(StaffStates.searching, F.text.in_(STAFF_MENU_TEXTS))
@router.message(StaffStates.selecting_item, F.text.in_(STAFF_MENU_TEXTS))
@router.message(StaffStates.entering_qty, F.text.in_(STAFF_MENU_TEXTS))
@router.message(StaffStates.editing_qty, F.text.in_(STAFF_MENU_TEXTS))
@router.message(StaffStates.selecting_storage, F.text.in_(STAFF_MENU_TEXTS))
@router.message(StaffStates.selecting_reason, F.text.in_(STAFF_MENU_TEXTS))
@router.message(StaffStates.entering_comment, F.text.in_(STAFF_MENU_TEXTS))
async def fsm_menu_interrupt(message: Message, state: FSMContext, staff: Staff) -> None:
    """Handle menu button presses while in any FSM state — route to correct handler."""
    await _safe_clear(state)
    text = message.text
    if text == "🔍 Додати товар":
        await btn_add_item(message, state)
    elif text == "🛒 Кошик":
        await btn_show_basket(message, state)
    elif text == "📤 Надіслати на підтвердження":
        await btn_send_basket(message, state, staff)
    elif text == "📜 Історія":
        await btn_staff_history(message, state)
    elif text == "❓ Допомога":
        await btn_help_staff(message, state)


async def _safe_clear(state: FSMContext) -> None:
    """Clear FSM state but preserve basket data."""
    data = await state.get_data()
    basket = data.get("basket", [])
    await state.clear()
    if basket:
        await state.update_data(basket=basket)


@router.message(F.text == "🔍 Додати товар")
async def btn_add_item(message: Message, state: FSMContext) -> None:
    await _safe_clear(state)
    await state.update_data(search_results={}, search_results_list=[], search_page=0)
    await message.answer("🔍 Введіть назву товару для пошуку:", reply_markup=cancel_kb())
    await state.set_state(StaffStates.searching)


@router.message(F.text == "🛒 Кошик")
async def btn_show_basket(message: Message, state: FSMContext) -> None:
    await _safe_clear(state)
    basket = await _get_basket(state)
    if not basket:
        await message.answer("🛒 Кошик порожній. Додайте товари через 🔍 Додати товар")
        return
    lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
    await message.answer(
        "🛒 <b>Кошик:</b>\n\n" + "\n".join(lines),
        reply_markup=basket_kb(basket),
    )


@router.message(F.text == "📤 Надіслати на підтвердження")
async def btn_send_basket(message: Message, state: FSMContext, staff: Staff) -> None:
    basket = await _get_basket(state)
    if not basket:
        await message.answer("🛒 Кошик порожній. Спочатку додайте товари.")
        return

    poster = PosterClient(await _get_client_token(staff.client_id))
    storages = await poster.get_storages()
    await poster.aclose()

    if len(storages) > 1:
        await state.set_state(StaffStates.selecting_storage)
        lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
        await message.answer(
            "🛒 <b>Кошик:</b>\n\n" + "\n".join(lines) + "\n\nОберіть склад списання:",
            reply_markup=storages_kb(storages),
        )
        return

    storage_id = int(storages[0]["storage_id"]) if storages else 1
    await state.update_data(storage_id=storage_id)
    await _ask_reason_msg(message, state, staff, basket)


async def _ask_reason_msg(message: Message, state: FSMContext, staff: Staff, basket: list[dict]) -> None:
    async with AsyncSessionLocal() as session:
        reasons = await ReasonRepo(session).list_by_client(staff.client_id)
    if reasons:
        await state.set_state(StaffStates.selecting_reason)
        lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
        await message.answer(
            "🛒 <b>Кошик:</b>\n\n" + "\n".join(lines) + "\n\nОберіть причину списання:",
            reply_markup=reasons_kb(reasons),
        )
    else:
        await state.update_data(poster_reason_id=None, reason_name=None)
        await state.set_state(StaffStates.entering_comment)
        await message.answer("💬 Додайте коментар або пропустіть:", reply_markup=skip_comment_kb())


@router.callback_query(F.data == "add_item")
async def cb_add_item(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(search_results={}, search_results_list=[], search_page=0)
    await call.message.answer("🔍 Введіть назву товару для пошуку:", reply_markup=cancel_kb())
    await state.set_state(StaffStates.searching)
    await call.answer()


@router.message(StaffStates.searching)
async def handle_search(message: Message, state: FSMContext, staff: Staff) -> None:
    query = message.text.strip()
    client_id = staff.client_id

    if matcher.is_stale(client_id):
        async with AsyncSessionLocal() as session:
            client = await session.get(Client, client_id)
        if client:
            poster = PosterClient(client.access_token)
            import asyncio as _asyncio
            raw_leftovers, raw_products, raw_mods = await _asyncio.gather(
                poster.get_storage_leftovers(),
                poster.get_products(),
                poster.get_modificators(),
                return_exceptions=True,
            )
            await poster.aclose()
            catalog = build_catalog(
                parse_leftovers(raw_leftovers if isinstance(raw_leftovers, list) else []),
                parse_products(raw_products if isinstance(raw_products, list) else []),
                parse_modificators(raw_mods if isinstance(raw_mods, list) else []),
            )
            matcher.update_catalog(client_id, catalog)

    results = matcher.search(query, client_id)
    if not results:
        await message.answer(
            f"😕 Нічого не знайдено за запитом «{query}».\nСпробуйте інакше:",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(
        # Composite key {id}_{type}: ingredient_id alone collides across the
        # ingredient/product ID spaces, which would resolve a tap to the wrong item.
        search_results={f"{r['ingredient_id']}_{r.get('item_type', '4')}": r for r in results},
        search_results_list=results,
        search_page=0,
    )
    total = len(results)
    head = "Оберіть товар:" if total <= PAGE_SIZE else f"Оберіть товар (знайдено {total}):"
    header = f"{head}\n<i>{SEARCH_LEGEND}</i>"
    await message.answer(
        header,
        reply_markup=search_results_kb(results, page=0, page_size=PAGE_SIZE),
    )
    await state.set_state(StaffStates.selecting_item)


@router.callback_query(StaffStates.selecting_item, F.data.startswith("search_page_"))
async def cb_search_page(call: CallbackQuery, state: FSMContext) -> None:
    page = int(call.data.removeprefix("search_page_"))
    data = await state.get_data()
    results = data.get("search_results_list", [])
    await state.update_data(search_page=page)
    total = len(results)
    head = "Оберіть товар:" if total <= PAGE_SIZE else f"Оберіть товар (знайдено {total}):"
    header = f"{head}\n<i>{SEARCH_LEGEND}</i>"
    await call.message.edit_text(
        header,
        reply_markup=search_results_kb(results, page=page, page_size=PAGE_SIZE),
    )
    await call.answer()


@router.callback_query(StaffStates.selecting_item, F.data.startswith("select_"))
async def cb_select_item(call: CallbackQuery, state: FSMContext) -> None:
    # callback: select_{ingredient_id}_{item_type} → composite key "{id}_{type}"
    key = call.data.removeprefix("select_")
    data = await state.get_data()
    item = data.get("search_results", {}).get(key)
    if not item:
        await call.answer("❌ Товар не знайдено", show_alert=True)
        return

    await state.update_data(selected_item=item)
    unit = item.get("unit", "")
    unit_hint = f" ({unit})" if unit else ""
    tag = _type_tag(item.get("item_type", "4"))
    await call.message.answer(
        f"{tag} · <b>{item['ingredient_name']}</b>\n\nВведіть кількість{unit_hint}:",
        reply_markup=cancel_kb(),
    )
    await state.set_state(StaffStates.entering_qty)
    await call.answer()


@router.message(StaffStates.entering_qty)
async def handle_qty(message: Message, state: FSMContext) -> None:
    text = message.text.strip().replace(",", ".")
    try:
        qty = float(text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введіть числове значення, наприклад: 2 або 0.5")
        return

    data = await state.get_data()
    item = data["selected_item"]
    basket = data.get("basket", [])

    # Merge only if the SAME item (id + type) is already in the basket — a bare
    # id match would wrongly merge two different items that share a numeric id.
    for b in basket:
        if b["ingredient_id"] == item["ingredient_id"] and b.get("item_type") == item.get("item_type", "4"):
            b["amount"] += qty
            break
    else:
        basket.append({
            "ingredient_id": item["ingredient_id"],
            "name": item["ingredient_name"],
            "amount": qty,
            "unit": item.get("unit", "шт"),
            "item_type": item.get("item_type", "4"),
        })

    await _set_basket(state, basket)
    await state.set_state(None)
    await message.answer(
        f"✅ Додано: <b>{item['ingredient_name']}</b> — {_fmt(qty)} {item.get('unit', 'шт')}\n"
        f"В кошику: {len(basket)} поз.",
        reply_markup=staff_menu_kb(),
    )


@router.callback_query(F.data == "show_basket")
async def cb_show_basket(call: CallbackQuery, state: FSMContext) -> None:
    basket = await _get_basket(state)
    if not basket:
        await call.answer("🛒 Кошик порожній", show_alert=True)
        return

    lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
    text = "🛒 *Ваш кошик:*\n\n" + "\n".join(lines)
    await call.message.answer(text, parse_mode="Markdown", reply_markup=basket_kb(basket))
    await call.answer()


@router.callback_query(F.data.startswith("remove_"))
async def cb_remove_item(call: CallbackQuery, state: FSMContext) -> None:
    ingredient_id = int(call.data.removeprefix("remove_"))
    basket = await _get_basket(state)
    basket = [b for b in basket if b["ingredient_id"] != ingredient_id]
    await _set_basket(state, basket)

    if not basket:
        await call.message.edit_text("🛒 Кошик порожній.")
        await call.answer()
        return

    lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
    text = "🛒 *Ваш кошик:*\n\n" + "\n".join(lines)
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=basket_kb(basket))
    await call.answer()


@router.callback_query(F.data.startswith("edit_qty_"))
async def cb_edit_qty(call: CallbackQuery, state: FSMContext) -> None:
    ingredient_id = int(call.data.removeprefix("edit_qty_"))
    basket = await _get_basket(state)
    item = next((b for b in basket if b["ingredient_id"] == ingredient_id), None)
    if not item:
        await call.answer("❌ Товар не знайдено", show_alert=True)
        return

    await state.update_data(editing_ingredient_id=ingredient_id)
    await state.set_state(StaffStates.editing_qty)
    unit_hint = f" ({item['unit']})" if item.get("unit") else ""
    await call.message.answer(
        f"✏️ <b>{item['name']}</b>\n"
        f"Поточна кількість: <b>{_fmt(item['amount'])} {item.get('unit', '')}</b>\n\n"
        f"Введіть нову кількість{unit_hint}:",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(StaffStates.editing_qty)
async def handle_edit_qty(message: Message, state: FSMContext) -> None:
    text = message.text.strip().replace(",", ".")
    try:
        qty = float(text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введіть числове значення, наприклад: 2 або 0.5")
        return

    data = await state.get_data()
    ingredient_id = data.get("editing_ingredient_id")
    basket = await _get_basket(state)

    for item in basket:
        if item["ingredient_id"] == ingredient_id:
            item["amount"] = qty
            updated_name = item["name"]
            updated_unit = item.get("unit", "")
            break
    else:
        await message.answer("❌ Товар не знайдено в кошику.", reply_markup=staff_menu_kb())
        await state.set_state(None)
        return

    await _set_basket(state, basket)
    await state.set_state(None)

    lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
    await message.answer(
        f"✅ Оновлено: <b>{updated_name}</b> — {_fmt(qty)} {updated_unit}\n\n"
        "🛒 <b>Кошик:</b>\n\n" + "\n".join(lines),
        reply_markup=basket_kb(basket),
    )


@router.callback_query(F.data == "clear_basket")
async def cb_clear_basket(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "🗑 Очистити кошик?\n\nВсі товари буде видалено.",
        reply_markup=clear_basket_confirm_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "clear_basket_confirm")
async def cb_clear_basket_confirm(call: CallbackQuery, state: FSMContext) -> None:
    await _set_basket(state, [])
    await call.message.edit_text("🗑 Кошик очищено.")
    await call.answer()


@router.callback_query(F.data == "cancel_search")
async def cb_cancel_search(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await call.message.answer("Скасовано.", reply_markup=staff_menu_kb())
    await call.answer()


@router.callback_query(F.data == "cancel_send")
async def cb_cancel_send(call: CallbackQuery, state: FSMContext) -> None:
    """Cancel storage/reason/comment selection, keep basket intact."""
    await _safe_clear(state)
    await call.message.answer("Відправку скасовано. Кошик збережено.", reply_markup=staff_menu_kb())
    await call.answer()


async def _basket_summary(basket: list[dict]) -> str:
    lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
    return "🛒 <b>Кошик:</b>\n\n" + "\n".join(lines)


@router.callback_query(F.data == "send_basket")
async def cb_send_basket(call: CallbackQuery, state: FSMContext, staff: Staff) -> None:
    basket = await _get_basket(state)
    if not basket:
        await call.answer("🛒 Кошик порожній", show_alert=True)
        return

    # Step 1: choose storage (skip if only one)
    poster = PosterClient((await _get_client_token(staff.client_id)))
    storages = await poster.get_storages()
    await poster.aclose()

    if len(storages) > 1:
        await state.set_state(StaffStates.selecting_storage)
        await call.message.answer(
            await _basket_summary(basket) + "\n\nОберіть склад списання:",
            reply_markup=storages_kb(storages),
        )
        await call.answer()
        return

    # Only one storage — save and proceed to reason
    storage_id = int(storages[0]["storage_id"]) if storages else 1
    await state.update_data(storage_id=storage_id)
    await _ask_reason(call, state, staff, basket)
    await call.answer()


async def _get_client_token(client_id: int) -> str:
    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        return client.access_token if client else ""


@router.callback_query(StaffStates.selecting_storage, F.data.startswith("storage_"))
async def cb_select_storage(call: CallbackQuery, state: FSMContext, staff: Staff) -> None:
    storage_id = int(call.data.removeprefix("storage_"))
    await state.update_data(storage_id=storage_id)
    basket = await _get_basket(state)
    await _ask_reason(call, state, staff, basket)
    await call.answer()


async def _ask_reason(call: CallbackQuery, state: FSMContext, staff: Staff, basket: list[dict]) -> None:
    async with AsyncSessionLocal() as session:
        reasons = await ReasonRepo(session).list_by_client(staff.client_id)

    if reasons:
        await state.set_state(StaffStates.selecting_reason)
        await call.message.answer(
            await _basket_summary(basket) + "\n\nОберіть причину списання:",
            reply_markup=reasons_kb(reasons),
        )
    else:
        await state.update_data(poster_reason_id=None, reason_name=None)
        await _ask_comment(call, state, basket)


async def _do_send_basket(
    event,
    state: FSMContext,
    staff: Staff,
    poster_reason_id: int | None,
    reason_name: str | None = None,
    storage_id: int | None = None,
    comment: str | None = None,
) -> None:
    basket = await _get_basket(state)

    async with AsyncSessionLocal() as session:
        client_repo = ClientRepo(session)
        writeoff_repo = WriteoffRepo(session)

        draft = await writeoff_repo.create_draft(staff.client_id, staff.tg_id, staff.name)
        draft.poster_reason_id = poster_reason_id
        draft.reason_name = reason_name
        draft.storage_id = storage_id
        draft.comment = comment
        for b in basket:
            await writeoff_repo.add_item(
                draft.id, b["ingredient_id"], b["name"], b["amount"], b["unit"],
                item_type=b.get("item_type", "4"),
            )
        await session.commit()
        admin_ids = await client_repo.get_admin_tg_ids(staff.client_id)

    await _set_basket(state, [])
    await state.set_state(None)

    msg = event if isinstance(event, Message) else event.message
    await msg.answer("✅ Запит надіслано адміністратору!", reply_markup=staff_menu_kb())

    lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
    extra = ""
    if reason_name:
        extra += f"\n📌 Причина: <b>{reason_name}</b>"
    if comment:
        extra += f"\n💬 Коментар: {comment}"
    admin_text = f"📋 <b>Запит на списання від {staff.name}</b>\n\n" + "\n".join(lines) + extra

    from bot.keyboards.admin_kb import confirm_reject_kb
    bot = msg.bot
    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id,
                admin_text,
                reply_markup=confirm_reject_kb(draft.id),
            )
        except Exception:
            pass


@router.callback_query(StaffStates.selecting_reason, F.data.startswith("reason_"))
async def cb_select_reason(call: CallbackQuery, state: FSMContext, staff: Staff) -> None:
    data = call.data.removeprefix("reason_")
    if data == "none":
        await state.update_data(poster_reason_id=None, reason_name=None)
    else:
        # format: reason_{db_id}_{poster_reason_id}
        db_id_str, poster_id_str = data.split("_", 1)
        db_id = int(db_id_str)
        poster_reason_id = int(poster_id_str)
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as _sel
            from db.models import WriteoffReason
            result = await session.execute(_sel(WriteoffReason).where(WriteoffReason.id == db_id))
            reason = result.scalar_one_or_none()
        await state.update_data(poster_reason_id=poster_reason_id, reason_name=reason.name if reason else None)

    basket = await _get_basket(state)
    await _ask_comment(call, state, basket)
    await call.answer()


async def _ask_comment(call: CallbackQuery, state: FSMContext, basket: list[dict]) -> None:
    await state.set_state(StaffStates.entering_comment)
    await call.message.answer(
        "💬 Додайте коментар до списання або пропустіть:",
        reply_markup=skip_comment_kb(),
    )


@router.message(StaffStates.entering_comment)
async def handle_comment(message: Message, state: FSMContext, staff: Staff) -> None:
    comment = message.text.strip() if message.text else None
    await _finalize_send(message, state, staff, comment)


@router.callback_query(StaffStates.entering_comment, F.data == "comment_skip")
async def cb_comment_skip(call: CallbackQuery, state: FSMContext, staff: Staff) -> None:
    await _finalize_send(call, state, staff, comment=None)
    await call.answer()


async def _finalize_send(event, state: FSMContext, staff: Staff, comment: str | None) -> None:
    data = await state.get_data()
    await _do_send_basket(
        event, state, staff,
        poster_reason_id=data.get("poster_reason_id"),
        reason_name=data.get("reason_name"),
        storage_id=data.get("storage_id"),
        comment=comment,
    )


@router.callback_query(F.data.startswith("resubmit_"))
async def cb_resubmit(call: CallbackQuery, state: FSMContext, staff: Staff) -> None:
    """Restore rejected draft items into basket so staff can re-send."""
    draft_id = int(call.data.removeprefix("resubmit_"))

    async with AsyncSessionLocal() as session:
        draft = await WriteoffRepo(session).get_with_items(draft_id)

    if not draft:
        await call.answer("❌ Запит не знайдено", show_alert=True)
        return

    if draft.staff_tg_id != call.from_user.id:
        await call.answer("⛔ Це не ваш запит", show_alert=True)
        return

    basket = [
        {
            "ingredient_id": item.ingredient_id,
            "name": item.ingredient_name,
            "amount": float(item.amount),
            "unit": item.unit,
            "item_type": item.item_type,
        }
        for item in draft.items
    ]

    await _safe_clear(state)
    await _set_basket(state, basket)

    lines = [f"• {b['name']} — {_fmt(b['amount'])} {b['unit']}" for b in basket]
    reject_block = ""
    if draft.reject_reason:
        reject_block = f"❌ <b>Причина відхилення:</b> {draft.reject_reason}\n\n"
    await call.message.answer(
        f"{reject_block}"
        "🔄 <b>Товари повернено до кошика:</b>\n\n" + "\n".join(lines) +
        "\n\nВи можете змінити склад або одразу надіслати знову.",
        reply_markup=staff_menu_kb(),
    )
    await call.answer()


# ──────────────────────────────────────────────
# Історія співробітника
# ──────────────────────────────────────────────

async def _show_staff_history(message: Message, tg_id: int, offset: int = 0) -> None:
    async with AsyncSessionLocal() as session:
        repo = WriteoffRepo(session)
        total = await repo.count_staff_history(tg_id)
        drafts = await repo.get_staff_history(tg_id, limit=STAFF_HISTORY_PAGE_SIZE, offset=offset)

    if not drafts:
        await message.answer("📜 Ваша історія запитів порожня.")
        return

    lines = []
    status_labels = {
        "pending":   "⏳ Очікує",
        "confirmed": "✅ Підтверджено",
        "rejected":  "❌ Відхилено",
        "cancelled": "🚫 Скасовано",
    }
    for draft in drafts:
        date_str = draft.created_at.strftime("%d.%m.%Y %H:%M")
        status = status_labels.get(draft.status, draft.status)
        item_names = ", ".join(
            f"{i.ingredient_name} {_fmt(float(i.amount))} {i.unit}"
            for i in draft.items
        ) if draft.items else "—"
        lines.append(f"<b>{date_str}</b> · {status}\n{item_names}")
        if draft.status == "rejected" and draft.reject_reason:
            lines.append(f"  └ Причина: {draft.reject_reason}")

    await message.answer(
        "📜 <b>Мої запити:</b>\n\n" + "\n\n".join(lines),
        reply_markup=staff_history_kb(drafts, offset, total),
    )


@router.message(F.text == "📜 Історія")
async def btn_staff_history(message: Message, state: FSMContext) -> None:
    await _safe_clear(state)
    await _show_staff_history(message, message.from_user.id, offset=0)


@router.callback_query(F.data.startswith("staff_history_page_"))
async def cb_staff_history_page(call: CallbackQuery) -> None:
    offset = int(call.data.removeprefix("staff_history_page_"))
    await _show_staff_history(call.message, call.from_user.id, offset=offset)
    await call.answer()


@router.callback_query(F.data.startswith("cancel_draft_"))
async def cb_cancel_draft(call: CallbackQuery) -> None:
    draft_id = int(call.data.removeprefix("cancel_draft_"))

    async with AsyncSessionLocal() as session:
        repo = WriteoffRepo(session)
        draft = await repo.get_with_items(draft_id)

        if not draft:
            await call.answer("❌ Запит не знайдено", show_alert=True)
            return
        if draft.staff_tg_id != call.from_user.id:
            await call.answer("⛔ Це не ваш запит", show_alert=True)
            return
        if draft.status != "pending":
            await call.answer("ℹ️ Запит вже оброблено", show_alert=True)
            return

        admin_ids = []
        from db.repositories.clients import ClientRepo
        admin_ids = await ClientRepo(session).get_admin_tg_ids(draft.client_id)
        staff_name = draft.staff_name

        await repo.cancel(draft)
        await session.commit()

    await call.answer("✅ Запит скасовано")
    await call.message.edit_text(
        call.message.text + "\n\n🚫 <i>Запит скасовано</i>",
        reply_markup=None,
    )

    # Notify admins
    for admin_id in admin_ids:
        try:
            await call.bot.send_message(
                admin_id,
                f"🚫 <b>{staff_name}</b> скасував(ла) запит на списання.",
            )
        except Exception:
            pass
