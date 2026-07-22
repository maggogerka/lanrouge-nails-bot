"""Restart-safe reminder worker using PostgreSQL leases instead of long sleeps."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import socket
from uuid import uuid4

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

from app.config import RuntimeConfigurationError, Settings, get_settings
from app.database import Database
from app.logging import configure_logging, log_event
from app.repositories import SqlAlchemyUnitOfWork
from app.schemas.notification import NotificationDelivery
from app.services.notification_service import NotificationService
from app.workers.reminder_messages import reminder_keyboard, render_reminder

logger = logging.getLogger(__name__)


def worker_identity() -> str:
    """Create a non-PII lease owner label unique to this process lifetime."""

    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"[:128]


def retry_delay_seconds(attempts: int) -> int:
    """Bound exponential retry delays to one hour."""

    exponent = min(max(0, attempts - 1), 8)
    return min(15 * (1 << exponent), 3600)


async def process_delivery(
    bot: Bot,
    service: NotificationService,
    delivery: NotificationDelivery,
    worker_id: str,
) -> None:
    try:
        await bot.send_message(
            delivery.recipient_telegram_id,
            render_reminder(delivery),
            reply_markup=reminder_keyboard(delivery),
        )
    except TelegramRetryAfter as exc:
        await service.retry(
            delivery.job_id,
            worker_id,
            delay_seconds=math.ceil(exc.retry_after) + 1,
            error_code="telegram_retry_after",
        )
    except TelegramForbiddenError:
        await service.mark_recipient_blocked(delivery.job_id, worker_id)
    except (TelegramNetworkError, TelegramServerError):
        await service.retry(
            delivery.job_id,
            worker_id,
            delay_seconds=retry_delay_seconds(delivery.attempts),
            error_code="telegram_temporary_error",
        )
    except TelegramAPIError:
        await service.mark_permanent_failure(
            delivery.job_id,
            worker_id,
            error_code="telegram_permanent_error",
        )
    except Exception:
        await service.retry(
            delivery.job_id,
            worker_id,
            delay_seconds=retry_delay_seconds(delivery.attempts),
            error_code="unexpected_delivery_error",
        )
        log_event(
            logger,
            logging.ERROR,
            "reminder.delivery_unexpected_error",
            appointment_id=delivery.appointment_id,
        )
    else:
        await service.mark_sent(delivery.job_id, worker_id)


async def run_worker(settings: Settings) -> None:
    settings.validate_worker_runtime()
    database = Database.create(settings.database_url.get_secret_value())
    service = NotificationService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        lease_seconds=settings.reminder_lease_seconds,
        max_attempts=settings.reminder_max_attempts,
    )
    worker_id = worker_identity()
    log_event(logger, logging.INFO, "reminder.worker_started")
    try:
        async with Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ) as bot:
            while True:
                job_ids = await service.claim_due(
                    worker_id,
                    limit=settings.reminder_batch_size,
                )
                if not job_ids:
                    await asyncio.sleep(settings.reminder_poll_interval_seconds)
                    continue
                for job_id in job_ids:
                    delivery = await service.prepare_delivery(job_id, worker_id)
                    if delivery is not None:
                        await process_delivery(bot, service, delivery, worker_id)
    finally:
        await database.close()
        log_event(logger, logging.INFO, "reminder.worker_stopped")


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
