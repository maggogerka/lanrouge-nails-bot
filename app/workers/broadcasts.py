"""Rate-limited restart-safe broadcast worker."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable

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
from app.observability import ObservabilityConfigurationError, initialize_observability
from app.repositories import SqlAlchemyUnitOfWork
from app.runtime_health import RuntimeHeartbeat, open_component_heartbeat
from app.schemas.broadcast import BroadcastDelivery
from app.services.broadcast_delivery_service import BroadcastDeliveryService
from app.utils.telegram_text import fits_telegram_caption, require_telegram_message
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


MediaCheckpoint = Callable[[int], Awaitable[None]]


async def send_delivery(
    bot: Bot,
    delivery: BroadcastDelivery,
    *,
    checkpoint_media: MediaCheckpoint | None = None,
) -> int:
    require_telegram_message(delivery.text)
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
        if fits_telegram_caption(delivery.text):
            if delivery.media_checkpoint_message_id is not None:
                return delivery.media_checkpoint_message_id
            message = await bot.send_photo(
                delivery.recipient_telegram_id,
                delivery.media[0].telegram_file_id,
                caption=delivery.text,
                reply_markup=markup,
                parse_mode=None,
            )
            if checkpoint_media is not None:
                await checkpoint_media(message.message_id)
            return message.message_id
        if delivery.media_checkpoint_message_id is None:
            media_message = await bot.send_photo(
                delivery.recipient_telegram_id,
                delivery.media[0].telegram_file_id,
                parse_mode=None,
            )
            if checkpoint_media is not None:
                await checkpoint_media(media_message.message_id)
        text_message = await bot.send_message(
            delivery.recipient_telegram_id,
            delivery.text,
            reply_markup=markup,
            parse_mode=None,
        )
        return text_message.message_id
    caption = delivery.text if fits_telegram_caption(delivery.text) else None
    messages = []
    if delivery.media_checkpoint_message_id is None:
        messages = await bot.send_media_group(
            delivery.recipient_telegram_id,
            [
                InputMediaPhoto(
                    media=item.telegram_file_id,
                    caption=caption if index == 0 else None,
                    parse_mode=None,
                )
                for index, item in enumerate(delivery.media)
            ],
        )
        if checkpoint_media is not None:
            await checkpoint_media(messages[0].message_id)
    if caption is None:
        text_message = await bot.send_message(
            delivery.recipient_telegram_id,
            delivery.text,
            reply_markup=markup,
            parse_mode=None,
        )
        return text_message.message_id
    if markup is not None:
        await bot.send_message(
            delivery.recipient_telegram_id,
            "Выберите действие:",
            reply_markup=markup,
            parse_mode=None,
        )
    if messages:
        return messages[0].message_id
    if delivery.media_checkpoint_message_id is None:
        raise RuntimeError("broadcast media checkpoint is missing")
    return delivery.media_checkpoint_message_id


async def process_delivery(
    bot: Bot,
    service: BroadcastDeliveryService,
    delivery: BroadcastDelivery,
    worker_id: str,
) -> None:
    async def checkpoint_media(message_id: int) -> None:
        stored = await service.mark_media_sent(
            delivery.recipient_id,
            worker_id,
            telegram_message_id=message_id,
        )
        if not stored:
            raise RuntimeError("broadcast media checkpoint lease was lost")

    try:
        message_id = await send_delivery(bot, delivery, checkpoint_media=checkpoint_media)
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
    """Validate configuration and supervise the broadcast loop heartbeat."""

    settings.validate_worker_runtime()
    settings.validate_dependency_runtime()
    async with open_component_heartbeat(settings, "broadcasts") as heartbeat:
        await _run_worker(settings, heartbeat)


async def run_delivery_cycle(
    bot: Bot,
    service: BroadcastDeliveryService,
    worker_id: str,
    *,
    batch_size: int,
    delay_seconds: float,
) -> int:
    """Process one claimed batch completely before allowing a heartbeat."""

    recipient_ids = await service.claim_due(worker_id, limit=batch_size)
    for recipient_id in recipient_ids:
        delivery = await service.prepare_delivery(recipient_id, worker_id)
        if delivery is not None:
            await process_delivery(bot, service, delivery, worker_id)
            await asyncio.sleep(delay_seconds)
    return len(recipient_ids)


async def _run_worker(settings: Settings, heartbeat: RuntimeHeartbeat) -> None:
    database = Database.from_settings(settings)
    try:
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
        async with Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ) as bot:
            while True:
                processed = await run_delivery_cycle(
                    bot,
                    service,
                    worker_id,
                    batch_size=settings.reminder_batch_size,
                    delay_seconds=delay,
                )
                await heartbeat.beat()
                if processed == 0:
                    await asyncio.sleep(settings.reminder_poll_interval_seconds)
    finally:
        await database.close()
        log_event(logger, logging.INFO, "broadcast.worker_stopped")


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        initialize_observability(settings)
        asyncio.run(run_worker(settings))
    except RuntimeConfigurationError as exc:
        log_event(logger, logging.CRITICAL, "configuration.invalid", missing=exc.missing)
        raise SystemExit(2) from exc
    except ObservabilityConfigurationError as exc:
        log_event(
            logger,
            logging.CRITICAL,
            "observability.configuration_invalid",
            error_code="sentry_initialization_failed",
        )
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
