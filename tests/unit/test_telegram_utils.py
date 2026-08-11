"""Idempotent Telegram edit helper tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from app.utils.telegram import edit_text_safely


@pytest.mark.asyncio
async def test_identical_telegram_edit_is_treated_as_successful_noop() -> None:
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: message is not modified",
        )
    )

    changed = await edit_text_safely(message, "Без изменений")

    assert changed is False


@pytest.mark.asyncio
async def test_real_telegram_edit_error_is_not_hidden() -> None:
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: message to edit not found",
        )
    )

    with pytest.raises(TelegramBadRequest, match="message to edit not found"):
        await edit_text_safely(message, "Новый текст")
