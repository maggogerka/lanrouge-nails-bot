"""Validated application configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
import re
import stat
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.reference_retention import ReferenceRetentionPolicy

_ADMIN_ID_SEPARATOR = re.compile(r"[\s,;]+")
_CSV_SEPARATOR = re.compile(r"\s*,\s*")
_SAFE_NAMESPACE = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_SAFE_INSTANCE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")
_POSTGRESQL_ASYNC_PREFIX = "postgresql+asyncpg://"
_REDIS_PREFIXES = ("redis://", "rediss://", "unix://")
_MAX_SECRET_FILE_BYTES = 16_384


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
    bot_token_file: Path | None = Field(
        default=None,
        validation_alias="BOT_TOKEN_FILE",
        repr=False,
        exclude=True,
    )
    admin_telegram_ids_raw: str = Field(
        default="",
        validation_alias="ADMIN_TELEGRAM_IDS",
        repr=False,
        exclude=True,
    )
    database_url: SecretStr = Field(default=SecretStr(""), validation_alias="DATABASE_URL")
    database_url_file: Path | None = Field(
        default=None,
        validation_alias="DATABASE_URL_FILE",
        repr=False,
        exclude=True,
    )
    database_password_file: Path | None = Field(
        default=None,
        validation_alias="DATABASE_PASSWORD_FILE",
        repr=False,
        exclude=True,
    )
    redis_url: SecretStr = Field(default=SecretStr(""), validation_alias="REDIS_URL")
    redis_url_file: Path | None = Field(
        default=None,
        validation_alias="REDIS_URL_FILE",
        repr=False,
        exclude=True,
    )
    redis_password_file: Path | None = Field(
        default=None,
        validation_alias="REDIS_PASSWORD_FILE",
        repr=False,
        exclude=True,
    )
    instance_id: str = Field(default="default-instance", validation_alias="INSTANCE_ID")
    redis_namespace: str = Field(default="lanrouge", validation_alias="REDIS_NAMESPACE")
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
    sentry_dsn_file: Path | None = Field(
        default=None,
        validation_alias="SENTRY_DSN_FILE",
        repr=False,
        exclude=True,
    )
    vendor_support_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias="VENDOR_SUPPORT_URL",
    )
    vendor_support_name: str = Field(
        default="Техническая поддержка CRM",
        min_length=1,
        max_length=100,
        validation_alias="VENDOR_SUPPORT_NAME",
    )
    vendor_support_hours: str | None = Field(
        default=None,
        max_length=255,
        validation_alias="VENDOR_SUPPORT_HOURS",
    )
    vendor_support_instructions: str | None = Field(
        default=None,
        max_length=1000,
        validation_alias="VENDOR_SUPPORT_INSTRUCTIONS",
    )
    # Container listener; host exposure remains controlled by Compose and the TLS proxy.
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")  # nosec B104
    api_port: int = Field(default=8080, ge=1, le=65535, validation_alias="API_PORT")
    api_allowed_hosts_raw: str = Field(
        default="localhost,127.0.0.1",
        validation_alias="API_ALLOWED_HOSTS",
        repr=False,
    )
    mini_app_allowed_origins_raw: str = Field(
        default="",
        validation_alias="MINI_APP_ALLOWED_ORIGINS",
        repr=False,
    )
    api_rate_limit_subject_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="API_RATE_LIMIT_SUBJECT_KEY",
    )
    api_rate_limit_subject_key_file: Path | None = Field(
        default=None,
        validation_alias="API_RATE_LIMIT_SUBJECT_KEY_FILE",
        repr=False,
        exclude=True,
    )
    api_session_signing_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="API_SESSION_SIGNING_KEY",
    )
    api_session_signing_key_file: Path | None = Field(
        default=None,
        validation_alias="API_SESSION_SIGNING_KEY_FILE",
        repr=False,
        exclude=True,
    )
    api_enforce_https: bool = Field(default=True, validation_alias="API_ENFORCE_HTTPS")
    api_max_body_bytes: int = Field(
        default=65_536,
        ge=1024,
        le=1_048_576,
        validation_alias="API_MAX_BODY_BYTES",
    )
    api_readiness_timeout_seconds: float = Field(
        default=3.0,
        ge=0.1,
        le=10,
        validation_alias="API_READINESS_TIMEOUT_SECONDS",
    )
    telegram_init_data_ttl_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        validation_alias="TELEGRAM_INIT_DATA_TTL_SECONDS",
    )
    api_session_ttl_seconds: int = Field(
        default=900,
        ge=60,
        le=86_400,
        validation_alias="API_SESSION_TTL_SECONDS",
    )
    yookassa_shop_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="YOOKASSA_SHOP_ID",
    )
    yookassa_shop_id_file: Path | None = Field(
        default=None,
        validation_alias="YOOKASSA_SHOP_ID_FILE",
        repr=False,
        exclude=True,
    )
    yookassa_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="YOOKASSA_SECRET_KEY",
    )
    yookassa_secret_key_file: Path | None = Field(
        default=None,
        validation_alias="YOOKASSA_SECRET_KEY_FILE",
        repr=False,
        exclude=True,
    )
    yookassa_business_id: int = Field(
        default=1,
        gt=0,
        validation_alias="YOOKASSA_BUSINESS_ID",
    )
    yookassa_return_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias="YOOKASSA_RETURN_URL",
    )
    yookassa_webhook_retention_days: int = Field(
        default=30,
        ge=1,
        le=365,
        validation_alias="YOOKASSA_WEBHOOK_RETENTION_DAYS",
    )
    reminder_poll_interval_seconds: float = Field(
        default=15.0,
        gt=0,
        le=300,
        validation_alias="REMINDER_POLL_INTERVAL_SECONDS",
    )
    reminder_batch_size: int = Field(
        default=20,
        gt=0,
        le=200,
        validation_alias="REMINDER_BATCH_SIZE",
    )
    reminder_max_attempts: int = Field(
        default=5,
        gt=0,
        le=20,
        validation_alias="REMINDER_MAX_ATTEMPTS",
    )
    reminder_lease_seconds: int = Field(
        default=120,
        gt=0,
        le=3600,
        validation_alias="REMINDER_LEASE_SECONDS",
    )
    reference_completed_retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        validation_alias="REFERENCE_COMPLETED_RETENTION_DAYS",
    )
    reference_cancelled_retention_days: int = Field(
        default=7,
        ge=1,
        le=3650,
        validation_alias="REFERENCE_CANCELLED_RETENTION_DAYS",
    )
    reference_no_show_retention_days: int = Field(
        default=14,
        ge=1,
        le=3650,
        validation_alias="REFERENCE_NO_SHOW_RETENTION_DAYS",
    )
    reference_draft_retention_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        validation_alias="REFERENCE_DRAFT_RETENTION_HOURS",
    )
    reference_cleanup_interval_hours: int = Field(
        default=6,
        ge=1,
        le=168,
        validation_alias="REFERENCE_CLEANUP_INTERVAL_HOURS",
    )

    @field_validator(
        "privacy_policy_url",
        "sentry_dsn",
        "vendor_support_url",
        "vendor_support_hours",
        "vendor_support_instructions",
        "yookassa_return_url",
        mode="before",
    )
    @classmethod
    def empty_optional_values_are_none(cls, value: Any) -> Any:
        """Treat empty values from the example env file as unset."""

        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("vendor_support_name", mode="before")
    @classmethod
    def normalize_vendor_support_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("vendor_support_url")
    @classmethod
    def vendor_support_must_use_https(
        cls,
        value: AnyHttpUrl | None,
    ) -> AnyHttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("VENDOR_SUPPORT_URL must use HTTPS")
        return value

    @field_validator(
        "bot_token_file",
        "database_url_file",
        "database_password_file",
        "redis_url_file",
        "redis_password_file",
        "sentry_dsn_file",
        "api_rate_limit_subject_key_file",
        "api_session_signing_key_file",
        "yookassa_shop_id_file",
        "yookassa_secret_key_file",
        mode="before",
    )
    @classmethod
    def empty_secret_file_paths_are_none(cls, value: Any) -> Any:
        """Treat empty ``*_FILE`` values as unset instead of the current directory."""

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

    @field_validator("instance_id")
    @classmethod
    def validate_instance_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if _SAFE_INSTANCE_ID.fullmatch(normalized) is None:
            raise ValueError("INSTANCE_ID must be a safe 2-64 character identifier")
        return normalized

    @field_validator("redis_namespace")
    @classmethod
    def validate_redis_namespace(cls, value: str) -> str:
        normalized = value.strip().lower()
        if _SAFE_NAMESPACE.fullmatch(normalized) is None:
            raise ValueError("REDIS_NAMESPACE must be a safe 2-48 character identifier")
        return normalized

    @field_validator("api_host")
    @classmethod
    def validate_api_bind_host(cls, value: str) -> str:
        normalized = value.strip()
        if _HOST.fullmatch(normalized) is None or ":" in normalized:
            raise ValueError("API_HOST must be an IPv4 address or DNS host without a port")
        return normalized

    @field_validator("api_allowed_hosts_raw")
    @classmethod
    def validate_api_allowed_hosts(cls, value: str) -> str:
        hosts = cls._csv(value)
        if any(_HOST.fullmatch(host) is None or host == "*" for host in hosts):
            raise ValueError("API_ALLOWED_HOSTS must contain exact comma-separated hosts")
        return ",".join(host.lower() for host in hosts)

    @field_validator("mini_app_allowed_origins_raw")
    @classmethod
    def validate_mini_app_allowed_origins(cls, value: str) -> str:
        origins = cls._csv(value)
        normalized: list[str] = []
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("MINI_APP_ALLOWED_ORIGINS must contain absolute HTTPS origins")
            normalized.append(f"https://{parsed.netloc.lower()}")
        return ",".join(normalized)

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
    def resolve_secret_files(self) -> Self:
        """Resolve Docker/Kubernetes secret files without exposing their contents."""

        pairs = (
            ("bot_token", "bot_token_file", "BOT_TOKEN"),
            ("database_url", "database_url_file", "DATABASE_URL"),
            ("redis_url", "redis_url_file", "REDIS_URL"),
            ("sentry_dsn", "sentry_dsn_file", "SENTRY_DSN"),
            (
                "api_rate_limit_subject_key",
                "api_rate_limit_subject_key_file",
                "API_RATE_LIMIT_SUBJECT_KEY",
            ),
            (
                "api_session_signing_key",
                "api_session_signing_key_file",
                "API_SESSION_SIGNING_KEY",
            ),
            ("yookassa_shop_id", "yookassa_shop_id_file", "YOOKASSA_SHOP_ID"),
            (
                "yookassa_secret_key",
                "yookassa_secret_key_file",
                "YOOKASSA_SECRET_KEY",
            ),
        )
        for value_field, file_field, variable_name in pairs:
            file_path = getattr(self, file_field)
            if file_path is None:
                continue
            configured = getattr(self, value_field)
            direct_value = (
                configured.get_secret_value() if isinstance(configured, SecretStr) else ""
            )
            if direct_value:
                raise ValueError(f"{variable_name} and {variable_name}_FILE are mutually exclusive")
            setattr(
                self,
                value_field,
                SecretStr(self._read_secret_file(file_path, f"{variable_name}_FILE")),
            )
        return self

    @model_validator(mode="after")
    def resolve_connection_password_files(self) -> Self:
        """Inject mounted passwords into connection URLs without duplicating secrets."""

        pairs = (
            ("database_url", "database_password_file", "DATABASE_PASSWORD_FILE"),
            ("redis_url", "redis_password_file", "REDIS_PASSWORD_FILE"),
        )
        for url_field, file_field, variable_name in pairs:
            file_path = getattr(self, file_field)
            if file_path is None:
                continue
            configured_url = getattr(self, url_field).get_secret_value()
            if not configured_url:
                raise ValueError(f"{variable_name} requires its connection URL")
            password = self._read_secret_file(file_path, variable_name)
            setattr(
                self,
                url_field,
                SecretStr(self._replace_url_password(configured_url, password, variable_name)),
            )
        return self

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

    @staticmethod
    def _csv(value: str) -> tuple[str, ...]:
        normalized = value.strip()
        if not normalized:
            return ()
        items = tuple(item for item in _CSV_SEPARATOR.split(normalized) if item)
        if len(items) > 32 or len(set(items)) != len(items):
            raise ValueError("CSV configuration must contain unique values (maximum 32)")
        return items

    @staticmethod
    def _read_secret_file(path: Path, variable_name: str) -> str:
        """Read one small, regular UTF-8 secret file with bounded memory usage."""

        try:
            with path.open("rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError(f"{variable_name} must reference a regular file")
                if metadata.st_size > _MAX_SECRET_FILE_BYTES:
                    raise ValueError(
                        f"{variable_name} must not exceed {_MAX_SECRET_FILE_BYTES} bytes"
                    )
                raw = stream.read(_MAX_SECRET_FILE_BYTES + 1)
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError(f"{variable_name} cannot be read") from exc

        if len(raw) > _MAX_SECRET_FILE_BYTES:
            raise ValueError(f"{variable_name} must not exceed {_MAX_SECRET_FILE_BYTES} bytes")
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{variable_name} must contain valid UTF-8") from exc

        if value.endswith("\n"):
            value = value[:-1]
            if value.endswith("\r"):
                value = value[:-1]
        if not value or "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError(f"{variable_name} must contain one non-empty line")
        if value != value.strip():
            raise ValueError(f"{variable_name} must not contain surrounding whitespace")
        return value

    @staticmethod
    def _replace_url_password(url: str, password: str, variable_name: str) -> str:
        parsed = urlsplit(url)
        if "@" not in parsed.netloc:
            raise ValueError(f"{variable_name} requires a URL with a username")
        user_info, host_info = parsed.netloc.rsplit("@", maxsplit=1)
        username = user_info.split(":", maxsplit=1)[0]
        if not username and parsed.scheme != "redis":
            raise ValueError(f"{variable_name} requires a URL with a username")
        netloc = f"{username}:{quote(password, safe='')}@{host_info}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    @property
    def admin_telegram_ids(self) -> frozenset[int]:
        """Return the immutable set used as the only admin authority source."""

        if not self.admin_telegram_ids_raw:
            return frozenset()
        return frozenset(int(part) for part in self.admin_telegram_ids_raw.split(","))

    @property
    def api_allowed_hosts(self) -> tuple[str, ...]:
        return self._csv(self.api_allowed_hosts_raw)

    @property
    def mini_app_allowed_origins(self) -> tuple[str, ...]:
        return self._csv(self.mini_app_allowed_origins_raw)

    @property
    def timezone_info(self) -> ZoneInfo:
        """Return the validated business timezone."""

        return ZoneInfo(self.timezone)

    @property
    def reference_retention_policy(self) -> ReferenceRetentionPolicy:
        """Build the immutable policy lazily to keep settings import boundaries simple."""

        return ReferenceRetentionPolicy(
            completed_days=self.reference_completed_retention_days,
            cancelled_days=self.reference_cancelled_retention_days,
            no_show_days=self.reference_no_show_retention_days,
        )

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

    def validate_worker_runtime(self) -> None:
        """Check values used by the independent reminder worker."""

        missing: list[str] = []
        if not self.bot_token.get_secret_value():
            missing.append("BOT_TOKEN")
        if not self.database_url.get_secret_value():
            missing.append("DATABASE_URL")
        if missing:
            raise RuntimeConfigurationError(tuple(missing))

    def validate_api_runtime(self) -> None:
        """Check API-only secrets and exact public-origin restrictions."""

        missing = self._missing_connections()
        if not self.bot_token.get_secret_value():
            missing.insert(0, "BOT_TOKEN")
        if not self.api_allowed_hosts:
            missing.append("API_ALLOWED_HOSTS")
        if not self.mini_app_allowed_origins:
            missing.append("MINI_APP_ALLOWED_ORIGINS")
        if not self.api_rate_limit_subject_key.get_secret_value():
            missing.append("API_RATE_LIMIT_SUBJECT_KEY")
        if not self.api_session_signing_key.get_secret_value():
            missing.append("API_SESSION_SIGNING_KEY")
        if missing:
            raise RuntimeConfigurationError(tuple(missing))
        self._require_secret_length(
            "API_RATE_LIMIT_SUBJECT_KEY",
            self.api_rate_limit_subject_key,
        )
        self._require_secret_length(
            "API_SESSION_SIGNING_KEY",
            self.api_session_signing_key,
        )
        if self.app_env is AppEnvironment.PRODUCTION and not self.api_enforce_https:
            raise ValueError("API_ENFORCE_HTTPS must be true in production")

    def validate_yookassa_runtime(self) -> None:
        """Require provider credentials only for a process serving its webhook."""

        missing: list[str] = []
        if not self.yookassa_shop_id.get_secret_value():
            missing.append("YOOKASSA_SHOP_ID")
        if not self.yookassa_secret_key.get_secret_value():
            missing.append("YOOKASSA_SECRET_KEY")
        if self.yookassa_return_url is None:
            missing.append("YOOKASSA_RETURN_URL")
        if missing:
            raise RuntimeConfigurationError(tuple(missing))

    def validate_reservation_worker_runtime(self) -> None:
        """Reservation expiry uses PostgreSQL and Redis component heartbeats."""

        missing = self._missing_connections()
        if missing:
            raise RuntimeConfigurationError(tuple(missing))

    def _missing_connections(self) -> list[str]:
        missing: list[str] = []
        if not self.database_url.get_secret_value():
            missing.append("DATABASE_URL")
        if not self.redis_url.get_secret_value():
            missing.append("REDIS_URL")
        return missing

    @staticmethod
    def _require_secret_length(name: str, value: SecretStr) -> None:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError(f"{name} must contain at least 32 bytes")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache process settings."""

    return Settings()
