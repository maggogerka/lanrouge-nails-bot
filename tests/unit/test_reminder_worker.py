"""Reminder rendering, backoff and Telegram outcome handling tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.domain.enums import NotificationType
from app.keyboards.admin.appointments import AdminAppointmentCallback
from app.schemas.notification import NotificationDelivery
from app.workers.reminder_messages import reminder_keyboard, render_reminder
from app.workers.reminders import process_delivery, retry_delay_seconds


def delivery() -> NotificationDelivery:
    return NotificationDelivery(
        job_id=21,
        appointment_id=11,
        recipient_user_id=5,
        recipient_telegram_id=101,
        notification_type=NotificationType.CLIENT_REMINDER,
        offset_minutes=1440,
        attempts=2,
        service_name="Консультация <premium>",
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        timezone="Europe/Moscow",
        address="Дом <20>",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/example_studio",
        client_name="Анна",
        client_phone="+79991234567",
    )


def test_24_hour_client_reminder_is_safe_and_has_confirmation_button() -> None:
    item = delivery()

    text = render_reminder(item)
    keyboard = reminder_keyboard(item)

    assert "Консультация &lt;premium&gt;" in text
    assert "Дом &lt;20&gt;" in text
    assert "Подтверждаю визит" in keyboard.inline_keyboard[0][0].text


def test_admin_reminder_opens_appointment_instead_of_master_profile() -> None:
    item = delivery().model_copy(update={"notification_type": NotificationType.ADMIN_REMINDER})

    keyboard = reminder_keyboard(item)

    button = keyboard.inline_keyboard[0][0]
    assert button.text == "📋 Перейти к записи"
    assert button.url is None
    callback = AdminAppointmentCallback.unpack(button.callback_data or "")
    assert callback.action == "view"
    assert callback.appointment_id == item.appointment_id


def test_backoff_is_bounded() -> None:
    assert retry_delay_seconds(1) == 15
    assert retry_delay_seconds(2) == 30
    assert retry_delay_seconds(100) <= 3600


@pytest.mark.asyncio
async def test_retry_after_uses_server_delay_with_padding() -> None:
    item = delivery()
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

    await process_delivery(bot, service, item, "worker-1")

    service.retry.assert_awaited_once_with(
        21,
        "worker-1",
        delay_seconds=31,
        error_code="telegram_retry_after",
    )


@pytest.mark.asyncio
async def test_forbidden_marks_recipient_blocked() -> None:
    item = delivery()
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=TelegramForbiddenError(
            method=SendMessage(chat_id=101, text="safe"),
            message="forbidden",
        )
    )
    service = MagicMock()
    service.mark_recipient_blocked = AsyncMock()

    await process_delivery(bot, service, item, "worker-1")

    service.mark_recipient_blocked.assert_awaited_once_with(21, "worker-1")


@pytest.mark.asyncio
async def test_successful_send_is_marked_sent() -> None:
    item = delivery()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    service = MagicMock()
    service.mark_sent = AsyncMock()

    await process_delivery(bot, service, item, "worker-1")

    service.mark_sent.assert_awaited_once_with(21, "worker-1")
