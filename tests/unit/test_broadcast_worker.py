"""Telegram broadcast rate-limit and irreversible outcome handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.domain.enums import BroadcastButtonType, MediaType
from app.schemas.broadcast import BroadcastDelivery, BroadcastMediaView
from app.workers.broadcasts import process_delivery, send_delivery


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


def media(position: int = 0) -> BroadcastMediaView:
    return BroadcastMediaView(
        telegram_file_id=f"photo-{position}",
        telegram_file_unique_id=f"unique-{position}",
        media_type=MediaType.PHOTO,
        position=position,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [1024, 1025])
async def test_single_photo_respects_caption_boundary_without_losing_text(length: int) -> None:
    bot = MagicMock()
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=77))
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=78))
    item = delivery().model_copy(update={"text": "x" * length, "media": [media()]})

    await send_delivery(bot, item)

    photo_kwargs = bot.send_photo.await_args.kwargs
    if length == 1024:
        assert photo_kwargs["caption"] == item.text
        bot.send_message.assert_not_awaited()
    else:
        assert "caption" not in photo_kwargs
        bot.send_message.assert_awaited_once()
        assert bot.send_message.await_args.args[1] == item.text


@pytest.mark.asyncio
async def test_plain_broadcast_rejects_4097_utf16_units_before_telegram_call() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    item = delivery().model_copy(update={"text": "x" * 4095 + "😀"})

    with pytest.raises(ValueError, match="4096"):
        await send_delivery(bot, item)

    bot.send_message.assert_not_awaited()
