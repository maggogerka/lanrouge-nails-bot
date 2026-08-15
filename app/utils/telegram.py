"""Small Telegram API helpers for idempotent inline-menu refreshes."""

from __future__ import annotations

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from app.utils.telegram_text import fits_telegram_caption, require_telegram_message


async def edit_text_safely(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Edit a message and treat Telegram's identical-content response as success."""

    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).casefold():
            raise
        return False
    return True


async def answer_photo_with_html(
    message: Message,
    photo_file_id: str,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Keep a full HTML card when its rendered text is too long for a caption."""

    require_telegram_message(text, html=True)
    if fits_telegram_caption(text, html=True):
        await message.answer_photo(
            photo_file_id,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
        return
    await message.answer_photo(photo_file_id)
    await message.answer(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
