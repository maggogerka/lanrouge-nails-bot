"""Composition and lifecycle guards for the executable API process."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.__main__ import create_application, run
from app.api.application import ApiApplication
from app.api.contracts import AsgiMessage
from app.config import Settings


def api_settings() -> Settings:
    return Settings(
        _env_file=None,
        BOT_TOKEN="123456:telegram-secret",
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/db",
        REDIS_URL="redis://localhost:6379/0",
        API_ALLOWED_HOSTS="api.example.test",
        MINI_APP_ALLOWED_ORIGINS="https://mini.example.test",
        API_RATE_LIMIT_SUBJECT_KEY="r" * 32,
        API_SESSION_SIGNING_KEY="s" * 32,
        YOOKASSA_SHOP_ID="shop-123",
        YOOKASSA_SECRET_KEY="provider-secret",
        YOOKASSA_BUSINESS_ID=7,
        YOOKASSA_RETURN_URL="https://t.me/example_bot",
    )


@pytest.mark.asyncio
async def test_composition_opens_and_closes_owned_resources_only_in_lifespan() -> None:
    database = SimpleNamespace(
        engine=MagicMock(),
        sessions=MagicMock(),
        close=AsyncMock(),
    )
    redis = MagicMock()
    redis.aclose = AsyncMock()
    transport = MagicMock()
    transport.start = AsyncMock()
    transport.close = AsyncMock()
    transport.request = AsyncMock()

    with (
        patch("app.api.__main__.Database.create", return_value=database) as create_database,
        patch("app.api.__main__.Redis.from_url", return_value=redis) as create_redis,
        patch("app.api.__main__.AioHttpTransport", return_value=transport),
    ):
        application = create_application(api_settings())

    assert isinstance(application, ApiApplication)
    create_database.assert_called_once()
    create_redis.assert_called_once()
    transport.start.assert_not_awaited()
    incoming: list[AsgiMessage] = [
        {"type": "lifespan.startup"},
        {"type": "lifespan.shutdown"},
    ]
    sent: list[AsgiMessage] = []

    async def receive() -> AsgiMessage:
        return incoming.pop(0)

    async def send(message: AsgiMessage) -> None:
        sent.append(message)

    await application({"type": "lifespan"}, receive, send)

    transport.start.assert_awaited_once()
    transport.close.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    database.close.assert_awaited_once()
    assert sent[-1] == {"type": "lifespan.shutdown.complete"}


def test_run_uses_bounded_server_defaults_and_loopback_proxy_trust() -> None:
    settings = api_settings()
    application = MagicMock(spec=ApiApplication)

    with (
        patch("app.api.__main__.get_settings", return_value=settings),
        patch("app.api.__main__.configure_logging"),
        patch("app.api.__main__.create_application", return_value=application),
        patch("app.api.__main__.uvicorn.run") as uvicorn_run,
    ):
        run()

    args, kwargs = uvicorn_run.call_args
    assert args == (application,)
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8080
    assert kwargs["forwarded_allow_ips"] == ["127.0.0.1", "::1"]
    assert kwargs["proxy_headers"] is True
    assert kwargs["access_log"] is False
    assert kwargs["server_header"] is False
    assert kwargs["limit_concurrency"] == 200


def test_composition_allows_api_without_optional_yookassa() -> None:
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="123456:telegram-secret",
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/db",
        REDIS_URL="redis://localhost:6379/0",
        API_ALLOWED_HOSTS="api.example.test",
        MINI_APP_ALLOWED_ORIGINS="https://mini.example.test",
        API_RATE_LIMIT_SUBJECT_KEY="r" * 32,
        API_SESSION_SIGNING_KEY="s" * 32,
        YOOKASSA_SHOP_ID="",
        YOOKASSA_SECRET_KEY="",
    )

    database = SimpleNamespace(
        engine=MagicMock(),
        sessions=MagicMock(),
        close=AsyncMock(),
    )
    redis = MagicMock()
    redis.aclose = AsyncMock()
    with (
        patch("app.api.__main__.Database.create", return_value=database),
        patch("app.api.__main__.Redis.from_url", return_value=redis),
        patch("app.api.__main__.AioHttpTransport") as transport,
    ):
        application = create_application(settings)

    assert isinstance(application, ApiApplication)
    transport.assert_not_called()


def test_composition_rejects_partial_yookassa_before_creating_network_clients() -> None:
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="123456:telegram-secret",
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/db",
        REDIS_URL="redis://localhost:6379/0",
        API_ALLOWED_HOSTS="api.example.test",
        MINI_APP_ALLOWED_ORIGINS="https://mini.example.test",
        API_RATE_LIMIT_SUBJECT_KEY="r" * 32,
        API_SESSION_SIGNING_KEY="s" * 32,
        YOOKASSA_SHOP_ID="shop-only",
        YOOKASSA_SECRET_KEY="",
    )

    with (
        patch("app.api.__main__.Database.create") as create_database,
        pytest.raises(ValueError),
    ):
        create_application(settings)

    create_database.assert_not_called()
