"""Configuration parsing and secret-safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import RuntimeConfigurationError, Settings
from app.domain.reference_retention import ReferenceRetentionPolicy


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


def test_bot_runtime_lists_only_missing_variable_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("BOT_TOKEN", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(variable, raising=False)

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


def test_worker_runtime_requires_bot_and_database_but_not_redis() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        BOT_TOKEN="123456:development-token",
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/db",
    )

    settings.validate_worker_runtime()


def test_reference_retention_defaults_and_policy_are_typed() -> None:
    settings = make_settings()

    assert settings.reference_draft_retention_hours == 24
    assert settings.reference_cleanup_interval_hours == 6
    assert settings.reference_retention_policy == ReferenceRetentionPolicy(
        completed_days=30,
        cancelled_days=7,
        no_show_days=14,
    )


@pytest.mark.parametrize(
    "field",
    [
        "REFERENCE_COMPLETED_RETENTION_DAYS",
        "REFERENCE_CANCELLED_RETENTION_DAYS",
        "REFERENCE_NO_SHOW_RETENTION_DAYS",
        "REFERENCE_DRAFT_RETENTION_HOURS",
        "REFERENCE_CLEANUP_INTERVAL_HOURS",
    ],
)
def test_reference_retention_rejects_zero(field: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: "0"})


def test_cleanup_worker_needs_database_but_not_bot_or_redis() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/db",
    )

    settings.validate_database_runtime()


def test_api_runtime_parses_exact_hosts_origins_and_secret_keys() -> None:
    settings = make_settings(
        API_ALLOWED_HOSTS="bot.example.com,api.example.com:8443",
        MINI_APP_ALLOWED_ORIGINS="https://bot.example.com,https://mini.example.com/",
        API_RATE_LIMIT_SUBJECT_KEY="r" * 32,
        API_SESSION_SIGNING_KEY="s" * 32,
    )

    settings.validate_api_runtime()

    assert settings.api_allowed_hosts == (
        "bot.example.com",
        "api.example.com:8443",
    )
    assert settings.mini_app_allowed_origins == (
        "https://bot.example.com",
        "https://mini.example.com",
    )


@pytest.mark.parametrize(
    "origin",
    ["http://bot.example.com", "https://user@bot.example.com", "https://bot.example.com/path"],
)
def test_mini_app_origin_rejects_insecure_or_ambiguous_values(origin: str) -> None:
    with pytest.raises(ValidationError, match="HTTPS origins"):
        make_settings(MINI_APP_ALLOWED_ORIGINS=origin)


def test_api_runtime_lists_missing_profile_specific_values() -> None:
    settings = make_settings(
        API_ALLOWED_HOSTS="",
        MINI_APP_ALLOWED_ORIGINS="",
        API_RATE_LIMIT_SUBJECT_KEY="",
        API_SESSION_SIGNING_KEY="",
    )

    with pytest.raises(RuntimeConfigurationError) as error:
        settings.validate_api_runtime()

    assert error.value.missing == (
        "API_ALLOWED_HOSTS",
        "MINI_APP_ALLOWED_ORIGINS",
        "API_RATE_LIMIT_SUBJECT_KEY",
        "API_SESSION_SIGNING_KEY",
    )


def test_api_runtime_rejects_short_secrets_without_rendering_them() -> None:
    settings = make_settings(
        API_ALLOWED_HOSTS="bot.example.com",
        MINI_APP_ALLOWED_ORIGINS="https://bot.example.com",
        API_RATE_LIMIT_SUBJECT_KEY="too-short",
        API_SESSION_SIGNING_KEY="s" * 32,
    )

    with pytest.raises(ValueError, match="API_RATE_LIMIT_SUBJECT_KEY") as error:
        settings.validate_api_runtime()

    assert "too-short" not in str(error.value)


def test_yookassa_credentials_are_required_only_for_provider_runtime() -> None:
    settings = make_settings(YOOKASSA_SHOP_ID="", YOOKASSA_SECRET_KEY="")

    with pytest.raises(RuntimeConfigurationError) as error:
        settings.validate_yookassa_runtime()

    assert error.value.missing == (
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "YOOKASSA_RETURN_URL",
    )


def test_vendor_support_is_separate_and_https_only() -> None:
    settings = make_settings(
        VENDOR_SUPPORT_URL="https://vendor.example.test/help",
        VENDOR_SUPPORT_NAME="  CRM Support  ",
    )

    assert str(settings.vendor_support_url) == "https://vendor.example.test/help"
    assert settings.vendor_support_name == "CRM Support"

    with pytest.raises(ValidationError, match="VENDOR_SUPPORT_URL"):
        make_settings(VENDOR_SUPPORT_URL="http://vendor.example.test/help")


def test_runtime_secrets_can_be_loaded_from_bounded_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("BOT_TOKEN", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(variable, raising=False)
    token_file = tmp_path / "bot-token"
    database_file = tmp_path / "database-url"
    redis_file = tmp_path / "redis-url"
    token_file.write_text("123456:file-token\n", encoding="utf-8")
    database_file.write_text(
        "postgresql+asyncpg://user:file-password@localhost/db\n",
        encoding="utf-8",
    )
    redis_file.write_text("redis://:file-password@localhost:6379/0\n", encoding="utf-8")

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        BOT_TOKEN_FILE=token_file,
        DATABASE_URL_FILE=database_file,
        REDIS_URL_FILE=redis_file,
    )

    settings.validate_bot_runtime()
    assert settings.bot_token.get_secret_value() == "123456:file-token"
    assert "file-password" not in repr(settings)


def test_connection_password_files_replace_url_passwords(tmp_path: Path) -> None:
    database_password = tmp_path / "database-password"
    redis_password = tmp_path / "redis-password"
    database_password.write_text("db/new+secret", encoding="utf-8")
    redis_password.write_text("redis_new-secret", encoding="utf-8")

    settings = make_settings(
        DATABASE_URL="postgresql+asyncpg://user:stale@postgres:5432/app",
        DATABASE_PASSWORD_FILE=str(database_password),
        REDIS_URL="redis://:stale@redis:6379/0",
        REDIS_PASSWORD_FILE=str(redis_password),
    )

    assert (
        settings.database_url.get_secret_value()
        == "postgresql+asyncpg://user:db%2Fnew%2Bsecret@postgres:5432/app"
    )
    assert settings.redis_url.get_secret_value() == "redis://:redis_new-secret@redis:6379/0"
    assert "db/new+secret" not in repr(settings)


def test_empty_yookassa_return_url_is_unset() -> None:
    settings = make_settings(YOOKASSA_RETURN_URL="")

    assert settings.yookassa_return_url is None


def test_direct_secret_and_file_are_mutually_exclusive(tmp_path: Path) -> None:
    token_file = tmp_path / "bot-token"
    token_file.write_text("123456:file-token", encoding="utf-8")

    with pytest.raises(ValidationError, match=r"BOT_TOKEN.*mutually exclusive") as error:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            BOT_TOKEN="123456:direct-token",
            BOT_TOKEN_FILE=token_file,
        )

    assert "file-token" not in str(error.value)
    assert "direct-token" not in str(error.value)


@pytest.mark.parametrize("contents", ["", "two\nlines\n", " surrounding ", "bad\x00value"])
def test_secret_files_reject_ambiguous_contents_without_leaking_them(
    tmp_path: Path,
    contents: str,
) -> None:
    secret_file = tmp_path / "sensitive-name"
    secret_file.write_text(contents, encoding="utf-8")

    with pytest.raises(ValidationError, match="BOT_TOKEN_FILE") as error:
        Settings(_env_file=None, BOT_TOKEN_FILE=secret_file)  # type: ignore[call-arg]

    rendered = str(error.value)
    assert str(secret_file) not in rendered
    if contents:
        assert repr(contents) not in rendered
