import re
import uuid
from urllib.parse import urlencode

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.keyboards.admin_kb import connect_poster_kb
from bot.states import OnboardingStates
from core.config import settings
from db.repositories.clients import ClientRepo
from db.session import AsyncSessionLocal

router = Router()

# Poster subdomain: letters/digits/hyphens, 1-63 chars, no leading/trailing hyphen.
_ACCOUNT_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_poster_account(raw: str) -> str | None:
    """Extract the Poster account subdomain from free-form user input.

    Accepts "mycafe", "mycafe.joinposter.com" or a full URL to that domain.
    """
    text = raw.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = text.split("/")[0]
    if text.endswith(".joinposter.com"):
        text = text[: -len(".joinposter.com")]
    if not text or not _ACCOUNT_RE.match(text):
        return None
    return text


async def ask_poster_account(message: Message, state: FSMContext) -> None:
    await state.set_state(OnboardingStates.entering_poster_account)
    await message.answer(
        "🏪 Введіть назву вашого акаунту Poster (піддомен).\n\n"
        "Це те, що ви бачите в адресному рядку при вході в Poster: "
        "<code>назва.joinposter.com</code>.\n"
        "Наприклад, якщо адреса <code>mycafe.joinposter.com</code> — просто введіть "
        "<code>mycafe</code>.",
    )


def _not_a_command(message: Message) -> bool:
    """Excludes commands so a stray /start mid-flow falls through to its own handler."""
    return not (message.text or "").startswith("/")


@router.message(OnboardingStates.entering_poster_account, _not_a_command)
async def handle_poster_account(message: Message, state: FSMContext) -> None:
    account = normalize_poster_account(message.text or "")
    if not account:
        await message.answer(
            "❌ Некоректна назва акаунту.\n\n"
            "Введіть лише назву піддомену (без https:// і без .joinposter.com), "
            "наприклад: <code>mycafe</code>"
        )
        return

    await state.clear()

    token = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        await ClientRepo(session).save_pending_oauth(token, message.from_user.id)
        await session.commit()

    # Poster echoes `redirect_uri` back verbatim (appending its own `code`/
    # `account`), so embedding our token here IS how it survives the round
    # trip — Poster doesn't relay a separate `state` param back at all.
    # The token-exchange step is a different story: there Poster validates
    # `redirect_uri` against the EXACT static value registered in the app
    # settings, so that call must use the bare URI with no query string
    # (see web/routes.py) — mixing the two up is what broke this before.
    redirect = f"{settings.web_base_url}/oauth/callback?oauth_token={token}"
    query = urlencode({
        "application_id": settings.poster_app_id,
        "redirect_uri": redirect,
        "response_type": "code",
    })
    oauth_url = f"https://{account}.joinposter.com/api/auth?{query}"
    await message.answer(
        f"✅ Акаунт: <b>{account}.joinposter.com</b>\n\n"
        "Натисніть кнопку нижче та авторизуйтесь у Poster:\n"
        "⚠️ Посилання діє 10 хвилин.",
        reply_markup=connect_poster_kb(oauth_url),
    )
