"""Optional Sentry bootstrap with a strict, dependency-independent event scrubber."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from importlib import import_module
from typing import Any, Protocol, cast

from app import __version__
from app.config import Settings
from app.logging import sanitize_text

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "password",
    "secret",
    "token",
    "dsn",
    "database_url",
    "redis_url",
    "phone",
    "email",
    "username",
    "ip_address",
    "initdata",
    "init_data",
    "webhook",
    "payment",
    "provider_object",
    "card",
    "account_number",
)
_SENSITIVE_EXACT_KEYS = {"pan", "cvv", "cvc"}
_DROP_EVENT_KEYS = {"user", "server_name"}
_DROP_REQUEST_KEYS = {"data", "cookies", "query_string", "env"}
_DROP_FRAME_KEYS = {"vars"}


class SecretValue(Protocol):
    def get_secret_value(self) -> str: ...


class SentrySDK(Protocol):
    def init(self, **kwargs: object) -> object: ...


class ObservabilityConfigurationError(RuntimeError):
    """Sentry was requested but cannot be initialized safely."""


def initialize_observability(settings: Settings) -> bool:
    """Initialize the process-wide optional Sentry boundary from validated settings."""

    try:
        return init_sentry(
            settings.sentry_dsn,
            environment=settings.app_env.value,
            release=f"v{__version__}",
        )
    except ObservabilityConfigurationError:
        raise
    except Exception as exc:
        raise ObservabilityConfigurationError(
            "configured observability backend could not be initialized"
        ) from exc


def scrub_sentry_event(
    event: dict[str, Any], hint: Mapping[str, object] | None = None
) -> dict[str, Any]:
    """Return a detached event with request bodies, PII and credentials removed."""

    del hint
    copied = deepcopy(event)
    scrubbed = _scrub_mapping(copied, location="event")
    return scrubbed


def init_sentry(
    dsn: str | SecretValue | None,
    *,
    environment: str,
    release: str | None = None,
    traces_sample_rate: float = 0.0,
    sdk: SentrySDK | None = None,
) -> bool:
    """Initialize Sentry only when configured; never enable default PII collection."""

    raw_dsn = _secret(dsn).strip()
    if not raw_dsn:
        return False
    if not environment.strip() or len(environment) > 64:
        raise ValueError("environment must contain 1-64 characters")
    if not 0.0 <= traces_sample_rate <= 1.0:
        raise ValueError("traces_sample_rate must be between 0 and 1")
    active_sdk = sdk or _load_sentry_sdk()
    active_sdk.init(
        dsn=raw_dsn,
        environment=environment.strip(),
        release=release,
        send_default_pii=False,
        before_send=scrub_sentry_event,
        traces_sample_rate=traces_sample_rate,
    )
    return True


def _load_sentry_sdk() -> SentrySDK:
    try:
        module = import_module("sentry_sdk")
    except ImportError as exc:
        raise ObservabilityConfigurationError(
            "Sentry is configured but sentry-sdk is not installed"
        ) from exc
    if not hasattr(module, "init"):
        raise ObservabilityConfigurationError("installed sentry-sdk has no init entrypoint")
    return cast(SentrySDK, module)


def _secret(value: str | SecretValue | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.get_secret_value()


def _sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized in _SENSITIVE_EXACT_KEYS or any(
        fragment in normalized for fragment in _SENSITIVE_KEY_PARTS
    )


def _scrub_mapping(value: Mapping[str, Any], *, location: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = str(raw_key)
        normalized = key.casefold().replace("-", "_")
        if location == "event" and normalized in _DROP_EVENT_KEYS:
            continue
        if location == "request" and normalized in _DROP_REQUEST_KEYS:
            result[key] = "[redacted]"
            continue
        if location == "frame" and normalized in _DROP_FRAME_KEYS:
            continue
        if location == "exception" and normalized == "value":
            result[key] = "[redacted]"
            continue
        if _sensitive_key(normalized):
            result[key] = "[redacted]"
            continue
        child_location = _child_location(location, normalized)
        result[key] = _scrub_value(item, location=child_location)
    return result


def _scrub_value(value: Any, *, location: str) -> Any:
    if isinstance(value, Mapping):
        return _scrub_mapping(value, location=location)
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item, location=location) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(str(value))


def _child_location(parent: str, key: str) -> str:
    if key == "request":
        return "request"
    if key in {"frames", "stacktrace"} or parent == "frame":
        return "frame"
    if key in {"exception", "exceptions"} or parent == "exception":
        return "exception"
    return parent
