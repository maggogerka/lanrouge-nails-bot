"""Structured logging and redaction tests."""

from __future__ import annotations

import json
import logging

from app.logging import JsonFormatter, get_correlation_id, reset_correlation_id, set_correlation_id


def test_formatter_emits_json_context_and_redacts_secrets() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="connected to postgresql://user:raw-password@database/name",
        args=(),
        exc_info=None,
    )
    record.event = "database.connected"
    record.user_id = 42
    record.database_url = "postgresql://user:another-password@database/name"
    record.bot_token = "secret-token"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "database.connected"
    assert payload["user_id"] == 42
    assert payload["database_url"] == "***"
    assert payload["bot_token"] == "***"
    assert "raw-password" not in json.dumps(payload)
    assert "another-password" not in json.dumps(payload)
    assert "secret-token" not in json.dumps(payload)


def test_correlation_id_is_scoped_and_restored() -> None:
    assert get_correlation_id() is None

    token = set_correlation_id("request-1")
    try:
        assert get_correlation_id() == "request-1"
    finally:
        reset_correlation_id(token)

    assert get_correlation_id() is None
