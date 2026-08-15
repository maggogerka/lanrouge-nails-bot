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
    split_telegram_html,
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


def test_long_html_is_split_on_codepoints_with_balanced_tags_and_entities() -> None:
    value = f"<b>{'😀&amp;&lt;&gt;' * 900}</b>"

    chunks = split_telegram_html(value)

    assert len(chunks) > 1
    assert all(telegram_text_length(chunk, html=True) <= TELEGRAM_MESSAGE_LIMIT for chunk in chunks)
    assert all(chunk.startswith("<b>") and chunk.endswith("</b>") for chunk in chunks)


@pytest.mark.asyncio
async def test_very_long_photo_card_keeps_keyboard_only_on_last_html_chunk() -> None:
    message = MagicMock(spec=Message)
    message.answer_photo = AsyncMock(return_value=MagicMock(spec=Message))
    message.answer = AsyncMock(return_value=MagicMock(spec=Message))
    text = f"<b>{'😀 &amp; ' * 2500}</b>"
    keyboard = MagicMock()

    await answer_photo_with_html(message, "photo-id", text, reply_markup=keyboard)

    message.answer_photo.assert_awaited_once_with("photo-id")
    assert message.answer.await_count > 1
    calls = message.answer.await_args_list
    assert all(call.kwargs["reply_markup"] is None for call in calls[:-1])
    assert calls[-1].kwargs["reply_markup"] is keyboard
    assert all(
        telegram_text_length(call.args[0], html=True) <= TELEGRAM_MESSAGE_LIMIT for call in calls
    )
