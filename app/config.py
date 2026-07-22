"""Validated application configuration loaded from environment variables."""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from functools import lru_cache
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ADMIN_ID_SEPARATOR = re.compile(r"[\s,;]+")
_POSTGRESQL_ASYNC_PREFIX = "postgresql+asyncpg://"
_REDIS_PREFIXES = ("redis://", "rediss://", "unix://")


class AppEnvironment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class RuntimeConfigurationError(ValueError):
    """Raised when a process starts without its mandatory settings."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(f"Missing required environment variables: {', '.join(missing)}")


class Settings(BaseSettings):
    """Process configuration.

    Empty secret defaults allow the bootstrap-only ``/whoami`` workflow to be
    configured deliberately. Each entry point calls the matching runtime
    validation method before opening network connections.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    bot_token: SecretStr = Field(default=SecretStr(""), validation_alias="BOT_TOKEN")
    admin_telegram_ids_raw: str = Field(
        default="",
        validation_alias="ADMIN_TELEGRAM_IDS",
        repr=False,
        exclude=True,
    )
    database_url: SecretStr = Field(default=SecretStr(""), validation_alias="DATABASE_URL")
    redis_url: SecretStr = Field(default=SecretStr(""), validation_alias="REDIS_URL")
    timezone: str = Field(default="Europe/Moscow", validation_alias="TIMEZONE")
    app_env: AppEnvironment = Field(
        default=AppEnvironment.DEVELOPMENT,
        validation_alias="APP_ENV",
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    privacy_policy_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias="PRIVACY_POLICY_URL",
    )
    sentry_dsn: SecretStr | None = Field(default=None, validation_alias="SENTRY_DSN")

    @field_validator("privacy_policy_url", "sentry_dsn", mode="before")
    @classmethod
    def empty_optional_values_are_none(cls, value: Any) -> Any:
        """Treat empty values from the example env file as unset."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("admin_telegram_ids_raw")
    @classmethod
    def validate_admin_telegram_ids(cls, value: str) -> str:
        """Reject malformed or non-positive Telegram IDs during settings load."""

        normalized = value.strip()
        if not normalized:
            return ""
        parts = [part for part in _ADMIN_ID_SEPARATOR.split(normalized) if part]
        if any(not part.isdecimal() or int(part) <= 0 for part in parts):
            raise ValueError("ADMIN_TELEGRAM_IDS must contain positive integers")
        return ",".join(parts)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Validate an IANA timezone without fixing a numeric UTC offset."""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("TIMEZONE must be a valid IANA timezone") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Normalize and validate the configured Python logging level."""

        normalized = value.upper()
        if normalized not in logging.getLevelNamesMapping():
            raise ValueError("LOG_LEVEL must be a standard Python logging level")
        return normalized

    @model_validator(mode="after")
    def validate_connection_schemes(self) -> Self:
        """Fail early on synchronous or unsupported connection URLs."""

        database_url = self.database_url.get_secret_value()
        if database_url and not database_url.startswith(_POSTGRESQL_ASYNC_PREFIX):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg://")

        redis_url = self.redis_url.get_secret_value()
        if redis_url and not redis_url.startswith(_REDIS_PREFIXES):
            raise ValueError("REDIS_URL must use redis://, rediss:// or unix://")
        return self

    @property
    def admin_telegram_ids(self) -> frozenset[int]:
        """Return the immutable set used as the only admin authority source."""

        if not self.admin_telegram_ids_raw:
            return frozenset()
        return frozenset(int(part) for part in self.admin_telegram_ids_raw.split(","))

    @property
    def timezone_info(self) -> ZoneInfo:
        """Return the validated business timezone."""

        return ZoneInfo(self.timezone)

    def validate_bot_runtime(self) -> None:
        """Check values required before the Telegram polling process starts."""

        missing = self._missing_connections()
        if not self.bot_token.get_secret_value():
            missing.insert(0, "BOT_TOKEN")
        if missing:
            raise RuntimeConfigurationError(tuple(missing))

    def validate_dependency_runtime(self) -> None:
        """Check values required by migrations and dependency health checks."""

        missing = self._missing_connections()
        if missing:
            raise RuntimeConfigurationError(tuple(missing))

    def validate_database_runtime(self) -> None:
        """Check the single value required by the Alembic process."""

        if not self.database_url.get_secret_value():
            raise RuntimeConfigurationError(("DATABASE_URL",))

    def _missing_connections(self) -> list[str]:
        missing: list[str] = []
        if not self.database_url.get_secret_value():
            missing.append("DATABASE_URL")
        if not self.redis_url.get_secret_value():
            missing.append("REDIS_URL")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache process settings."""

    return Settings()
