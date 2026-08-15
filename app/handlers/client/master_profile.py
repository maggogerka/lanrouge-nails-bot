"""Published master profile shown to clients."""

from html import escape

from aiogram import F, Router
from aiogram.types import Message

from app.keyboards.client.main import CLIENT_MASTER_PROFILE_TEXT
from app.keyboards.client.master_profile import master_profile_links_keyboard
from app.schemas.master_profile import MasterProfileView
from app.services.master_profile_service import MasterProfileService
from app.utils.telegram import answer_photo_with_html

router = Router(name="client.master_profile")


@router.message(F.text == CLIENT_MASTER_PROFILE_TEXT)
async def show_master_profile(
    message: Message,
    master_profile_service: MasterProfileService,
) -> None:
    profile = await master_profile_service.get_public()
    if profile is None:
        await message.answer("Информация о мастере пока не опубликована.")
        return
    text = _render_profile(profile)
    keyboard = master_profile_links_keyboard(profile)
    if profile.telegram_photo_file_id:
        await answer_photo_with_html(
            message,
            profile.telegram_photo_file_id,
            text,
            reply_markup=keyboard,
        )
    else:
        await message.answer(text, reply_markup=keyboard)


def _render_profile(profile: MasterProfileView) -> str:
    parts = [f"<b>{escape(profile.display_name)}</b>"]
    if profile.bio:
        parts.append(escape(profile.bio))
    if profile.address:
        parts.append(f"📍 {escape(profile.address)}")
    return "\n\n".join(parts)
