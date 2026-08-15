"""Telegram text/caption boundary and safe media fallback regressions."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ParseMode
from aiogram.types import Message

from app.utils.telegram import answer_photo_with_html
from app.utils.telegram_text import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    fits_telegram_caption,
    require_telegram_message,
    telegram_text_length,
)


def test_plain_message_limits_count_utf16_emoji_and_html_characters() -> None:
    at_limit = "x" * (TELEGRAM_MESSAGE_LIMIT - 5) + "😀<>&"

    assert telegram_text_length(at_limit) == TELEGRAM_MESSAGE_LIMIT
    assert require_telegram_message(at_limit) == at_limit
    with pytest.raises(ValueError, match="4096"):
        require_telegram_message(at_limit + "!")


def test_html_caption_limits_count_rendered_text_not_markup_or_entities() -> None:
    at_limit = f"<b>{'x' * (TELEGRAM_CAPTION_LIMIT - 3)}😀&amp;</b>"

    assert telegram_text_length(at_limit, html=True) == TELEGRAM_CAPTION_LIMIT
    assert fits_telegram_caption(at_limit, html=True)
    assert not fits_telegram_caption(at_limit + "!", html=True)


@pytest.mark.asyncio
async def test_long_photo_text_is_not_truncated_or_put_into_caption() -> None:
    message = MagicMock(spec=Message)
    message.answer_photo = AsyncMock()
    message.answer = AsyncMock()
    text = f"<b>Мастер</b>\n{'x' * 1025}"
    keyboard = MagicMock()

    await answer_photo_with_html(message, "photo-id", text, reply_markup=keyboard)

    message.answer_photo.assert_awaited_once_with("photo-id")
    message.answer.assert_awaited_once_with(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
