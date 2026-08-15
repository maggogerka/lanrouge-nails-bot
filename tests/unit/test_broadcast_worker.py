"""Telegram broadcast rate-limit and irreversible outcome handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.domain.enums import BroadcastButtonType
from app.schemas.broadcast import BroadcastDelivery
from app.workers.broadcasts import process_delivery


def delivery() -> BroadcastDelivery:
    return BroadcastDelivery(
        recipient_id=41,
        broadcast_id=31,
        recipient_user_id=5,
        recipient_telegram_id=101,
        attempts=2,
        text="Plain <text>",
        button_type=BroadcastButtonType.NONE,
        media=[],
    )


@pytest.mark.asyncio
async def test_retry_after_schedules_retry_with_server_delay() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramRetryAfter(
            method=SendMessage(chat_id=101, text="safe"),
            message="retry",
            retry_after=30,
        )
    )
    service = MagicMock()
    service.retry = AsyncMock()

    await process_delivery(bot, service, delivery(), "worker")
    service.retry.assert_awaited_once_with(
        41, "worker", delay_seconds=31, error_code="telegram_retry_after"
    )


@pytest.mark.asyncio
async def test_permanent_error_is_not_retried() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramBadRequest(
            method=SendMessage(chat_id=101, text="safe"), message="bad request"
        )
    )
    service = MagicMock()
    service.mark_failed = AsyncMock()

    await process_delivery(bot, service, delivery(), "worker")
    service.mark_failed.assert_awaited_once_with(
        41, "worker", error_code="telegram_permanent_error"
    )


@pytest.mark.asyncio
async def test_forbidden_marks_recipient_blocked() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramForbiddenError(
            method=SendMessage(chat_id=101, text="safe"), message="forbidden"
        )
    )
    service = MagicMock()
    service.mark_blocked = AsyncMock()

    await process_delivery(bot, service, delivery(), "worker")
    service.mark_blocked.assert_awaited_once_with(41, "worker")
