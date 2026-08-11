"""Opaque Redis Mini App session tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.api.sessions import (
    OpaqueSessionIssuer,
    RedisOpaqueSessionStore,
    SessionStoreError,
)
from app.api.telegram_auth import VerifiedWebAppIdentity

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)
TOKEN = "A" * 43


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.set_results: list[object] = []
        self.error: Exception | None = None

    async def set(self, name: str, value: str, *, ex: int, nx: bool) -> object:
        if self.error is not None:
            raise self.error
        if self.set_results:
            result = self.set_results.pop(0)
            if result is not True:
                return result
        if nx and name in self.values:
            return False
        self.values[name] = value
        self.ttls[name] = ex
        return True

    async def get(self, name: str) -> object:
        if self.error is not None:
            raise self.error
        return self.values.get(name)

    async def delete(self, *names: str) -> object:
        if self.error is not None:
            raise self.error
        deleted = 0
        for name in names:
            deleted += int(self.values.pop(name, None) is not None)
            self.ttls.pop(name, None)
        return deleted


def identity() -> VerifiedWebAppIdentity:
    return VerifiedWebAppIdentity(
        telegram_user_id=123_456,
        auth_date=NOW - timedelta(seconds=10),
        session_fingerprint="f" * 64,
        start_parameter="campaign_2026",
    )


def build_store(redis: FakeRedis) -> RedisOpaqueSessionStore:
    return RedisOpaqueSessionStore(
        redis,
        SecretStr("separate-session-pepper-with-32-bytes"),
    )


@pytest.mark.asyncio
async def test_issuer_returns_only_random_opaque_token_and_hashes_redis_key() -> None:
    redis = FakeRedis()
    store = build_store(redis)
    issuer = OpaqueSessionIssuer(store, ttl_seconds=900, clock=lambda: NOW)

    with patch("app.api.sessions.secrets.token_urlsafe", return_value=TOKEN):
        response = await issuer.issue_session(identity(), correlation_id="request-123")

    assert response == {
        "session_token": TOKEN,
        "token_type": "Bearer",
        "expires_in": 900,
    }
    assert "telegram" not in json.dumps(response).lower()
    [(key, value)] = redis.values.items()
    assert key.startswith("telegram_crm:webapp_session:")
    assert key != TOKEN
    assert TOKEN not in key
    assert TOKEN not in value
    assert redis.ttls[key] == 900

    server_session = await store.resolve(TOKEN, now=NOW)
    assert server_session is not None
    assert server_session.telegram_user_id == 123_456
    assert server_session.start_parameter == "campaign_2026"


@pytest.mark.asyncio
async def test_token_collision_retries_without_overwriting_existing_session() -> None:
    redis = FakeRedis()
    redis.set_results = [False, True]
    store = build_store(redis)
    issuer = OpaqueSessionIssuer(store, ttl_seconds=300, clock=lambda: NOW)
    second_token = "B" * 43

    with patch(
        "app.api.sessions.secrets.token_urlsafe",
        side_effect=[TOKEN, second_token],
    ):
        response = await issuer.issue_session(identity(), correlation_id="request-123")

    assert response["session_token"] == second_token
    assert len(redis.values) == 1


@pytest.mark.asyncio
async def test_expired_session_is_deleted_and_cannot_be_resolved() -> None:
    redis = FakeRedis()
    store = build_store(redis)
    issuer = OpaqueSessionIssuer(store, ttl_seconds=60, clock=lambda: NOW)
    with patch("app.api.sessions.secrets.token_urlsafe", return_value=TOKEN):
        await issuer.issue_session(identity(), correlation_id="request-123")

    result = await store.resolve(TOKEN, now=NOW + timedelta(seconds=61))

    assert result is None
    assert not redis.values


@pytest.mark.asyncio
async def test_revoke_is_idempotent_without_disclosing_session() -> None:
    redis = FakeRedis()
    store = build_store(redis)
    issuer = OpaqueSessionIssuer(store, ttl_seconds=300, clock=lambda: NOW)
    with patch("app.api.sessions.secrets.token_urlsafe", return_value=TOKEN):
        await issuer.issue_session(identity(), correlation_id="request-123")

    assert await store.revoke(TOKEN)
    assert not await store.revoke(TOKEN)
    assert await store.resolve(TOKEN, now=NOW) is None


@pytest.mark.asyncio
async def test_redis_failures_are_safe_and_fail_closed() -> None:
    redis = FakeRedis()
    redis.error = OSError("redis://user:super-secret@example.test")
    store = build_store(redis)
    issuer = OpaqueSessionIssuer(store, ttl_seconds=300, clock=lambda: NOW)

    with (
        patch("app.api.sessions.secrets.token_urlsafe", return_value=TOKEN),
        pytest.raises(SessionStoreError) as raised,
    ):
        await issuer.issue_session(identity(), correlation_id="request-123")

    assert "super-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_malformed_server_record_fails_closed() -> None:
    redis = FakeRedis()
    store = build_store(redis)
    issuer = OpaqueSessionIssuer(store, ttl_seconds=300, clock=lambda: NOW)
    with patch("app.api.sessions.secrets.token_urlsafe", return_value=TOKEN):
        await issuer.issue_session(identity(), correlation_id="request-123")
    key = next(iter(redis.values))
    redis.values[key] = '{"telegram_user_id":123456,"telegram_user_id":999999}'

    with pytest.raises(SessionStoreError):
        await store.resolve(TOKEN, now=NOW)


def test_session_pepper_and_token_format_are_fail_fast() -> None:
    redis = FakeRedis()
    with pytest.raises(ValueError, match="32"):
        RedisOpaqueSessionStore(redis, SecretStr("too-short"))

    store = build_store(redis)
    with pytest.raises(ValueError, match="opaque"):
        store._validate_token("telegram-id-123456")
