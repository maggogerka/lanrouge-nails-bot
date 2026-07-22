"""Structured JSON logging with safe contextual fields."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Final

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_CREDENTIAL_IN_URL: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@]+@",
    flags=re.IGNORECASE,
)
_SENSITIVE_KEYS: Final[tuple[str, ...]] = (
    "token",
    "password",
    "secret",
    "database_url",
    "redis_url",
    "phone",
    "comment",
)
_STANDARD_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    set(logging.makeLogRecord({}).__dict__)
    | {
        "message",
        "asctime",
        "event",
    }
)


def set_correlation_id(value: str) -> Token[str | None]:
    """Bind a correlation ID to the current asynchronous context."""

    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    """Restore the previous correlation context."""

    _correlation_id.reset(token)


def get_correlation_id() -> str | None:
    """Return the current request correlation ID."""

    return _correlation_id.get()


def _redact_url_credentials(value: str) -> str:
    return _CREDENTIAL_IN_URL.sub(r"\g<prefix>***@", value)


def _sanitize(key: str, value: Any) -> Any:
    if any(fragment in key.casefold() for fragment in _SENSITIVE_KEYS):
        return "***"
    if isinstance(value, str):
        return _redact_url_credentials(value)
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize(str(child_key), child) for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize(key, item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    """Render one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": _redact_url_credentials(getattr(record, "event", record.getMessage())),
        }

        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_KEYS and not key.startswith("_"):
                payload[key] = _sanitize(key, value)

        if record.exc_info:
            payload["exception"] = _redact_url_credentials(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Configure application and dependency loggers once per process."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **context: Any,
) -> None:
    """Log an event with allow-listed, machine-readable context supplied by callers."""

    logger.log(level, event, extra={"event": event, **context})
