"""Rate-limited restart-safe broadcast worker."""

from __future__ import annotations

import asyncio
import logging
import math

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from app.config import RuntimeConfigurationError, Settings, get_settings
from app.database import Database
from app.domain.enums import BroadcastButtonType
from app.keyboards.client.marketing import MarketingCallback
from app.logging import configure_logging, log_event
from app.repositories import SqlAlchemyUnitOfWork
from app.schemas.broadcast import BroadcastDelivery
from app.services.broadcast_delivery_service import BroadcastDeliveryService
from app.workers.reminders import retry_delay_seconds, worker_identity

logger = logging.getLogger(__name__)


def delivery_keyboard(delivery: BroadcastDelivery) -> InlineKeyboardMarkup | None:
    if delivery.button_type is BroadcastButtonType.NONE:
        return None
    text = delivery.button_text or "Открыть"
    if delivery.button_type is BroadcastButtonType.URL and delivery.button_url:
        button = InlineKeyboardButton(text=text, url=delivery.button_url)
    else:
        button = InlineKeyboardButton(
            text=text,
            callback_data=MarketingCallback(
                action=delivery.button_type.value,
                broadcast_id=delivery.broadcast_id,
            ).pack(),
        )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


async def send_delivery(bot: Bot, delivery: BroadcastDelivery) -> int:
    markup = delivery_keyboard(delivery)
    if not delivery.media:
        message = await bot.send_message(
            delivery.recipient_telegram_id,
            delivery.text,
            reply_markup=markup,
            parse_mode=None,
        )
        return message.message_id
    if len(delivery.media) == 1:
        message = await bot.send_photo(
            delivery.recipient_telegram_id,
            delivery.media[0].telegram_file_id,
            caption=delivery.text,
            reply_markup=markup,
            parse_mode=None,
        )
        return message.message_id
    messages = await bot.send_media_group(
        delivery.recipient_telegram_id,
        [
            InputMediaPhoto(
                media=item.telegram_file_id,
                caption=delivery.text if index == 0 else None,
                parse_mode=None,
            )
            for index, item in enumerate(delivery.media)
        ],
    )
    if markup is not None:
        await bot.send_message(
            delivery.recipient_telegram_id,
            "Выберите действие:",
            reply_markup=markup,
            parse_mode=None,
        )
    return messages[0].message_id


async def process_delivery(
    bot: Bot,
    service: BroadcastDeliveryService,
    delivery: BroadcastDelivery,
    worker_id: str,
) -> None:
    try:
        message_id = await send_delivery(bot, delivery)
    except TelegramRetryAfter as exc:
        await service.retry(
            delivery.recipient_id,
            worker_id,
            delay_seconds=math.ceil(exc.retry_after) + 1,
            error_code="telegram_retry_after",
        )
    except TelegramForbiddenError:
        await service.mark_blocked(delivery.recipient_id, worker_id)
    except (TelegramNetworkError, TelegramServerError):
        await service.retry(
            delivery.recipient_id,
            worker_id,
            delay_seconds=retry_delay_seconds(delivery.attempts),
            error_code="telegram_temporary_error",
        )
    except TelegramAPIError:
        await service.mark_failed(
            delivery.recipient_id,
            worker_id,
            error_code="telegram_permanent_error",
        )
    except Exception:
        await service.retry(
            delivery.recipient_id,
            worker_id,
            delay_seconds=retry_delay_seconds(delivery.attempts),
            error_code="unexpected_delivery_error",
        )
        log_event(logger, logging.ERROR, "broadcast.delivery_unexpected_error")
    else:
        await service.mark_sent(delivery.recipient_id, worker_id, telegram_message_id=message_id)


async def run_worker(settings: Settings) -> None:
    settings.validate_worker_runtime()
    database = Database.create(settings.database_url.get_secret_value())
    async with SqlAlchemyUnitOfWork(database.sessions) as uow:
        business_settings = await uow.settings.get()
        if business_settings is None:
            raise RuntimeError("Business settings are missing")
        max_attempts = business_settings.broadcast_max_retries
        messages_per_second = business_settings.broadcast_messages_per_second
    service = BroadcastDeliveryService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        lease_seconds=settings.reminder_lease_seconds,
        max_attempts=max_attempts,
    )
    worker_id = worker_identity()
    delay = 1 / max(1, messages_per_second)
    log_event(logger, logging.INFO, "broadcast.worker_started")
    try:
        async with Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ) as bot:
            while True:
                recipient_ids = await service.claim_due(
                    worker_id, limit=settings.reminder_batch_size
                )
                if not recipient_ids:
                    await asyncio.sleep(settings.reminder_poll_interval_seconds)
                    continue
                for recipient_id in recipient_ids:
                    delivery = await service.prepare_delivery(recipient_id, worker_id)
                    if delivery is not None:
                        await process_delivery(bot, service, delivery, worker_id)
                        await asyncio.sleep(delay)
    finally:
        await database.close()
        log_event(logger, logging.INFO, "broadcast.worker_stopped")


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(run_worker(settings))
    except RuntimeConfigurationError as exc:
        log_event(logger, logging.CRITICAL, "configuration.invalid", missing=exc.missing)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
