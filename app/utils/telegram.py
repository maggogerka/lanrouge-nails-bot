"""Small Telegram API helpers for idempotent inline-menu refreshes."""

from __future__ import annotations

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto, Message

from app.utils.telegram_text import fits_telegram_caption, split_telegram_html


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


async def edit_photo_safely(
    message: Message,
    media: InputMediaPhoto,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Edit a photo card and accept Telegram's identical-content response as a no-op."""

    try:
        await message.edit_media(media, reply_markup=reply_markup)
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
) -> list[Message]:
    """Keep a full HTML card when its rendered text is too long for a caption."""

    if fits_telegram_caption(text, html=True):
        sent = await message.answer_photo(
            photo_file_id,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
        )
        return [sent]
    sent_messages = [await message.answer_photo(photo_file_id)]
    chunks = split_telegram_html(text)
    for index, chunk in enumerate(chunks):
        sent_messages.append(
            await message.answer(
                chunk,
                reply_markup=reply_markup if index == len(chunks) - 1 else None,
                parse_mode=ParseMode.HTML,
            )
        )
    return sent_messages


async def answer_html_safely(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> list[Message]:
    """Send valid HTML in bounded chunks, keeping controls on the final message."""

    chunks = split_telegram_html(text)
    sent_messages: list[Message] = []
    for index, chunk in enumerate(chunks):
        sent_messages.append(
            await message.answer(
                chunk,
                reply_markup=reply_markup if index == len(chunks) - 1 else None,
                parse_mode=ParseMode.HTML,
            )
        )
    return sent_messages
