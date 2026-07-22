"""Configuration parsing and secret-safety tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import RuntimeConfigurationError, Settings


def make_settings(**overrides: str) -> Settings:
    values = {
        "BOT_TOKEN": "123456:development-token",
        "DATABASE_URL": "postgresql+asyncpg://user:password@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "TIMEZONE": "Europe/Moscow",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_admin_ids_are_positive_unique_integers() -> None:
    settings = make_settings(ADMIN_TELEGRAM_IDS="123, 456;123")

    assert settings.admin_telegram_ids == frozenset({123, 456})


@pytest.mark.parametrize("value", ["name", "-12", "1,two", "0"])
def test_invalid_admin_ids_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError, match="positive integers"):
        make_settings(ADMIN_TELEGRAM_IDS=value)


def test_connection_schemes_are_validated() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg"):
        make_settings(DATABASE_URL="postgresql://localhost/db")

    with pytest.raises(ValidationError, match="REDIS_URL"):
        make_settings(REDIS_URL="http://localhost:6379")


def test_timezone_is_validated_with_zoneinfo() -> None:
    settings = make_settings(TIMEZONE="Europe/Moscow")

    assert settings.timezone_info.key == "Europe/Moscow"

    with pytest.raises(ValidationError, match="IANA timezone"):
        make_settings(TIMEZONE="Mars/Olympus")


def test_empty_optional_urls_are_none() -> None:
    settings = make_settings(PRIVACY_POLICY_URL="", SENTRY_DSN="")

    assert settings.privacy_policy_url is None
    assert settings.sentry_dsn is None


def test_bot_runtime_lists_only_missing_variable_names() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(RuntimeConfigurationError) as error:
        settings.validate_bot_runtime()

    assert error.value.missing == ("BOT_TOKEN", "DATABASE_URL", "REDIS_URL")
    assert "password" not in str(error.value).casefold()


def test_secret_values_are_hidden_from_repr() -> None:
    settings = make_settings()
    rendered = repr(settings)

    assert "development-token" not in rendered
    assert "password" not in rendered
    assert "**********" in rendered
