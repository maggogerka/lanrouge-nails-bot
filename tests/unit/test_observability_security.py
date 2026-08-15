"""Strict Sentry and structured-log redaction tests."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.logging import JsonFormatter
from app.observability import (
    ObservabilityConfigurationError,
    init_sentry,
    initialize_observability,
    scrub_sentry_event,
)


class FakeSentry:
    def __init__(self) -> None:
        self.options: dict[str, object] | None = None

    def init(self, **kwargs: object) -> object:
        self.options = kwargs
        return object()


def test_sentry_is_disabled_without_dsn_and_never_calls_sdk() -> None:
    sdk = FakeSentry()

    enabled = init_sentry(None, environment="production", sdk=sdk)

    assert not enabled
    assert sdk.options is None


def test_sentry_initialization_forces_no_pii_and_installs_scrubber() -> None:
    sdk = FakeSentry()

    enabled = init_sentry(
        SecretStr("https://public@example.test/1"),
        environment="production",
        release="v0.4.1",
        traces_sample_rate=0.05,
        sdk=sdk,
    )

    assert enabled
    assert sdk.options is not None
    assert sdk.options["send_default_pii"] is False
    assert sdk.options["before_send"] is scrub_sentry_event
    assert sdk.options["environment"] == "production"


def test_settings_observability_uses_environment_and_package_release() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="production",
        SENTRY_DSN="https://public@example.test/1",
    )

    with patch("app.observability.init_sentry", return_value=True) as initialize:
        enabled = initialize_observability(settings)

    assert enabled
    initialize.assert_called_once_with(
        settings.sentry_dsn,
        environment="production",
        release="v0.4.1",
    )


def test_settings_observability_wraps_backend_failure_without_secret_text() -> None:
    settings = Settings(
        _env_file=None,
        SENTRY_DSN="https://public:private@example.test/1",
    )

    with (
        patch(
            "app.observability.init_sentry",
            side_effect=RuntimeError("https://public:private@example.test/1"),
        ),
        pytest.raises(ObservabilityConfigurationError) as caught,
    ):
        initialize_observability(settings)

    assert "private" not in str(caught.value)


def test_sentry_scrubber_removes_request_pii_frame_vars_and_inline_secrets() -> None:
    event: dict[str, Any] = {
        "event_id": "safe-id",
        "user": {"id": "telegram-777", "email": "client@example.test"},
        "request": {
            "url": "https://example.test/webhook?token=raw-token",
            "query_string": "initData=secret-init-data",
            "data": {"card": "4111 1111 1111 1111"},
            "cookies": {"session": "cookie-secret"},
            "headers": {
                "Authorization": "Bearer access-secret",
                "X-API-Key": "api-secret",
                "Accept": "application/json",
            },
        },
        "extra": {
            "payment_payload": {"provider_payment_id": "provider-secret"},
            "safe_count": 3,
            "message": "password=hunter2 card 5555-5555-5555-4444",
        },
        "exception": {
            "values": [
                {
                    "value": "Authorization: Bearer exception-secret",
                    "stacktrace": {
                        "frames": [{"function": "safe", "vars": {"token": "frame-secret"}}]
                    },
                }
            ]
        },
    }

    scrubbed = scrub_sentry_event(event)
    serialized = json.dumps(scrubbed)

    assert scrubbed["event_id"] == "safe-id"
    assert "user" not in scrubbed
    assert scrubbed["request"]["data"] == "[redacted]"
    assert scrubbed["request"]["query_string"] == "[redacted]"
    assert scrubbed["request"]["headers"]["Accept"] == "application/json"
    for secret in (
        "telegram-777",
        "client@example.test",
        "raw-token",
        "secret-init-data",
        "cookie-secret",
        "access-secret",
        "api-secret",
        "provider-secret",
        "hunter2",
        "5555-5555-5555-4444",
        "exception-secret",
        "frame-secret",
    ):
        assert secret not in serialized
    frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "vars" not in frame
    assert scrubbed["exception"]["values"][0]["value"] == "[redacted]"


def test_logging_redacts_headers_payment_webhook_initdata_cards_and_exception_text() -> None:
    try:
        raise RuntimeError(
            "Authorization: Bearer exception-secret client@example.test +7 999 123-45-67 "
            "card 4111 1111 1111 1111"
        )
    except RuntimeError:
        exception_info = sys.exc_info()
    record = logging.LogRecord(
        name="security-test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="request Authorization=Bearer message-secret api_key=query-secret",
        args=(),
        exc_info=exception_info,
    )
    record.authorization = "Bearer context-secret"
    record.cookie = "session=cookie-secret"
    record.webhook_body = {"payload": "webhook-secret"}
    record.payment_payload = {"card": "5555-5555-5555-4444"}
    record.initData = "init-secret"
    record.safe_count = 2

    payload = json.loads(JsonFormatter().format(record))
    serialized = json.dumps(payload)

    assert payload["safe_count"] == 2
    assert payload["authorization"] == "***"
    assert payload["webhook_body"] == "***"
    for secret in (
        "message-secret",
        "query-secret",
        "context-secret",
        "cookie-secret",
        "webhook-secret",
        "5555-5555-5555-4444",
        "init-secret",
        "exception-secret",
        "client@example.test",
        "+7 999 123-45-67",
        "4111 1111 1111 1111",
    ):
        assert secret not in serialized
