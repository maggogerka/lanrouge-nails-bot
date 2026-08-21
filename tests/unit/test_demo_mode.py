"""Public demo configuration, policy and relative-date guarantees."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

import pytest

from app.config import AppMode, RuntimeConfigurationError, Settings
from app.database.models.demo import DemoSession
from app.demo.policy import DemoActionBlocked, DemoOperation, DemoPolicy
from app.demo.seed import build_slot_seed
from app.demo.service import DemoService, DemoStaleAction


def demo_settings(**overrides: str) -> Settings:
    values = {
        "APP_MODE": "demo",
        "BOT_TOKEN": "123456:separate-demo-token",
        "DATABASE_URL": "postgresql+asyncpg://demo:password@localhost/crm_demo",
        "REDIS_URL": "redis://localhost:6379/1",
        "PRODUCTION_BOT_TOKEN_SHA256": sha256(b"production-token").hexdigest(),
        "PRODUCTION_DATABASE_URL_SHA256": sha256(b"production-database").hexdigest(),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_production_is_the_default_mode() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_mode is AppMode.PRODUCTION


def test_demo_requires_production_fingerprints() -> None:
    settings = demo_settings(
        PRODUCTION_BOT_TOKEN_SHA256="",
        PRODUCTION_DATABASE_URL_SHA256="",
    )

    with pytest.raises(RuntimeConfigurationError) as error:
        settings.validate_bot_runtime()

    assert error.value.missing == (
        "PRODUCTION_BOT_TOKEN_SHA256",
        "PRODUCTION_DATABASE_URL_SHA256",
    )


def test_demo_rejects_reused_token_and_database_without_rendering_secrets() -> None:
    token = "123456:separate-demo-token"
    database_url = "postgresql+asyncpg://demo:password@localhost/crm_demo"
    token_settings = demo_settings(
        PRODUCTION_BOT_TOKEN_SHA256=sha256(token.encode()).hexdigest()
    )
    with pytest.raises(ValueError, match="must differ") as token_error:
        token_settings.validate_bot_runtime()
    assert token not in str(token_error.value)

    database_settings = demo_settings(
        PRODUCTION_DATABASE_URL_SHA256=sha256(database_url.encode()).hexdigest()
    )
    with pytest.raises(ValueError, match="must differ") as database_error:
        database_settings.validate_bot_runtime()
    assert "password" not in str(database_error.value)


def test_mode_database_markers_fail_closed() -> None:
    with pytest.raises(ValueError, match="contain 'demo'"):
        demo_settings(DATABASE_URL="postgresql+asyncpg://demo@localhost/crm").validate_bot_runtime()

    production = Settings(  # type: ignore[call-arg]
        _env_file=None,
        BOT_TOKEN="123456:production",
        DATABASE_URL="postgresql+asyncpg://prod@localhost/crm_demo",
        REDIS_URL="redis://localhost:6379/0",
    )
    with pytest.raises(ValueError, match="refuses"):
        production.validate_bot_runtime()


def test_demo_policy_blocks_every_external_side_effect() -> None:
    policy = DemoPolicy()
    for operation in (
        DemoOperation.PAYMENT,
        DemoOperation.REFUND,
        DemoOperation.BROADCAST,
        DemoOperation.EXTERNAL_NOTIFICATION,
        DemoOperation.STAFF_INVITATION,
        DemoOperation.OWNER_BOOTSTRAP,
        DemoOperation.BACKUP,
        DemoOperation.PERSONAL_DATA_EXPORT,
        DemoOperation.FILE_UPLOAD,
        DemoOperation.PRODUCTION_API,
    ):
        with pytest.raises(DemoActionBlocked):
            policy.require(operation)


def test_seed_windows_are_relative_to_current_date() -> None:
    now = datetime(2031, 2, 10, 14, 20, tzinfo=UTC)
    slots = build_slot_seed(now, ZoneInfo("Europe/Moscow"))

    assert slots
    assert all(item.start_at > now for item in slots)
    assert max(item.start_at for item in slots) <= now + timedelta(days=15)
    assert len({item.start_at.date() for item in slots}) >= 7


def test_old_callback_generation_is_invalid_after_reset() -> None:
    workspace = DemoSession(
        telegram_user_id=100,
        generation=2,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )

    with pytest.raises(DemoStaleAction):
        DemoService._validate_workspace(workspace, generation=1)
