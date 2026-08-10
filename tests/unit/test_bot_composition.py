"""Composition-root tests without opening network connections."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.fsm.storage.redis import RedisStorage

from app.bot import create_dispatcher, run, run_polling
from app.config import Settings
from app.database import Database
from app.domain.enums import PaymentMode
from app.handlers import root_router
from app.handlers.errors import handle_unexpected_error
from app.observability import ObservabilityConfigurationError
from app.schemas.authorization import StaffBootstrapResult
from app.services.authorization_service import AuthorizationService


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        BOT_TOKEN="123456:development-token",
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/database",
        REDIS_URL="redis://localhost:6379/0",
        ADMIN_TELEGRAM_IDS="101",
    )


@pytest.mark.asyncio
async def test_dispatcher_uses_redis_and_includes_root_router() -> None:
    database = Database.create("postgresql+asyncpg://user:password@localhost/database")
    dispatcher = create_dispatcher(make_settings(), database)

    assert isinstance(dispatcher.storage, RedisStorage)
    assert dispatcher.storage.state_ttl == 24 * 60 * 60
    assert dispatcher.storage.data_ttl == 24 * 60 * 60
    assert root_router in dispatcher.sub_routers
    assert isinstance(dispatcher.workflow_data["authorization_service"], AuthorizationService)
    assert dispatcher.workflow_data["service_catalog"]._admin_telegram_ids == frozenset()
    assert "acquisition_service" in dispatcher.workflow_data
    assert "master_workspace_service" in dispatcher.workflow_data
    assert "vendor_support_service" in dispatcher.workflow_data

    await dispatcher.storage.close()
    await database.close()


def test_error_boundary_is_registered_on_root_router() -> None:
    callbacks = [handler.callback for handler in root_router.errors.handlers]

    assert handle_unexpected_error in callbacks


@pytest.mark.asyncio
async def test_polling_bootstraps_env_owners_before_dispatcher_runtime() -> None:
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="123456:development-token",
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/database",
        REDIS_URL="redis://localhost:6379/0",
        ADMIN_TELEGRAM_IDS="101,202",
    )
    database = MagicMock()
    database.close = AsyncMock()
    authorization_service = MagicMock(spec=AuthorizationService)
    authorization_service.bootstrap_owners = AsyncMock(
        return_value=StaffBootstrapResult(
            business_id=1,
            owner_already_present=True,
        )
    )
    dispatcher = MagicMock()
    dispatcher.storage.close = AsyncMock()
    dispatcher.start_polling = AsyncMock()
    dispatcher.resolve_used_update_types.return_value = []
    bot = MagicMock()
    bot.delete_webhook = AsyncMock()
    bot_context = MagicMock()
    bot_context.__aenter__ = AsyncMock(return_value=bot)
    bot_context.__aexit__ = AsyncMock(return_value=None)
    events: list[str] = []
    heartbeat = MagicMock()
    heartbeat.beat = AsyncMock(side_effect=lambda: events.append("heartbeat"))
    heartbeat.run_periodically = AsyncMock()
    bot.delete_webhook.side_effect = lambda **_: events.append("webhook")
    dispatcher.start_polling.side_effect = lambda *_args, **_kwargs: events.append("polling")

    @asynccontextmanager
    async def heartbeat_context(*_args: object, **_kwargs: object):
        yield heartbeat

    with (
        patch(
            "app.bot.check_dependencies",
            new=AsyncMock(side_effect=lambda *_: events.append("dependencies")),
        ),
        patch("app.bot.open_component_heartbeat", side_effect=heartbeat_context),
        patch("app.bot.Database.create", return_value=database) as create_database,
        patch("app.bot.AuthorizationService", return_value=authorization_service),
        patch("app.bot.create_dispatcher", return_value=dispatcher) as build_dispatcher,
        patch("app.bot.Bot", return_value=bot_context),
    ):
        await run_polling(settings)

    create_database.assert_called_once_with(settings.database_url.get_secret_value())
    authorization_service.bootstrap_owners.assert_awaited_once_with(
        business_id=1,
        telegram_ids=settings.admin_telegram_ids,
    )
    build_dispatcher.assert_called_once()
    dispatcher_args = build_dispatcher.call_args.args
    assert dispatcher_args[:3] == (settings, database, authorization_service)
    assert PaymentMode.MANUAL in dispatcher_args[3]
    dispatcher.start_polling.assert_awaited_once()
    dispatcher.storage.close.assert_awaited_once()
    database.close.assert_awaited_once()
    heartbeat.beat.assert_awaited_once()
    assert events[:4] == ["dependencies", "webhook", "heartbeat", "polling"]


def test_observability_failure_prevents_event_loop_start() -> None:
    configured = make_settings()

    with (
        patch("app.bot.get_settings", return_value=configured),
        patch("app.bot.configure_logging"),
        patch(
            "app.bot.initialize_observability",
            side_effect=ObservabilityConfigurationError("safe failure"),
        ),
        patch("app.bot.asyncio.run") as asyncio_run,
        pytest.raises(SystemExit) as caught,
    ):
        run()

    assert caught.value.code == 2
    asyncio_run.assert_not_called()
