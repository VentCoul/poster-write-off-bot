from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from bot.states import OnboardingStates
from core.config import settings
from db.models import Admin as AdminModel
from db.repositories.staff import StaffRepo
from db.session import AsyncSessionLocal


class StaffAuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            tg_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            tg_id = event.from_user.id if event.from_user else None
        else:
            return await handler(event, data)

        if not tg_id:
            return

        # Config-level admins (owner / super-admin)
        if tg_id in settings.admin_chat_ids:
            data["is_admin"] = True
            return await handler(event, data)

        # Pre-registration onboarding choice buttons — always pass
        if isinstance(event, CallbackQuery) and event.data in {"onboard_admin", "onboard_staff_code"}:
            return await handler(event, data)

        # Mid onboarding text entry (Poster domain / invite code) — always pass
        state: FSMContext | None = data.get("state")
        if state and await state.get_state() in {
            OnboardingStates.entering_poster_account.state,
            OnboardingStates.entering_invite_code.state,
        }:
            return await handler(event, data)

        # Check DB: admin or staff? Must run BEFORE the /start pass-through below,
        # otherwise a DB admin's /start slips past without is_admin being set and
        # falls through to the staff onboarding handler (shows the role picker).
        async with AsyncSessionLocal() as session:
            admin_row = (await session.execute(
                select(AdminModel).where(AdminModel.tg_id == tg_id)
            )).scalar_one_or_none()

            if admin_row:
                data["is_admin"] = True
                return await handler(event, data)

            staff = await StaffRepo(session).get_by_tg_id(tg_id)

        if staff:
            data["staff"] = staff
            return await handler(event, data)

        # Unknown user — /start passes (invite registration + new admin onboarding)
        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            return await handler(event, data)

        # Everything else from an unknown user — block
        if isinstance(event, Message):
            await event.answer(
                "👋 Вас не знайдено в системі.\n\n"
                "Зверніться до адміністратора — він надішле вам посилання для реєстрації."
            )
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔ Вас не знайдено в системі.", show_alert=True)
