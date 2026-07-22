"""Telegram long-polling process and dependency composition root."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.config import RuntimeConfigurationError, Settings, get_settings
from app.handlers import root_router
from app.healthcheck import check_dependencies
from app.logging import configure_logging, log_event
from app.middlewares.correlation import CorrelationIdMiddleware

logger = logging.getLogger(__name__)


def create_dispatcher(settings: Settings) -> Dispatcher:
    """Build a Dispatcher without opening Telegram connections."""

    storage = RedisStorage.from_url(
        settings.redis_url.get_secret_value(),
        state_ttl=60 * 60,
        data_ttl=60 * 60,
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher.update.outer_middleware(CorrelationIdMiddleware())
    dispatcher.include_router(root_router)
    return dispatcher


async def run_polling(settings: Settings) -> None:
    """Validate dependencies and run long polling until shutdown."""

    settings.validate_bot_runtime()
    await check_dependencies(settings)

    dispatcher = create_dispatcher(settings)
    if not settings.admin_telegram_ids:
        log_event(logger, logging.WARNING, "configuration.admin_ids_empty")

    log_event(
        logger,
        logging.INFO,
        "bot.starting",
        app_env=settings.app_env.value,
        admin_count=len(settings.admin_telegram_ids),
    )

    async with Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as bot:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
        finally:
            await dispatcher.storage.close()
            log_event(logger, logging.INFO, "bot.stopped")


def run() -> None:
    """Load settings and run the bot process."""

    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(run_polling(settings))
    except RuntimeConfigurationError as exc:
        log_event(logger, logging.CRITICAL, "configuration.invalid", missing=exc.missing)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    run()
