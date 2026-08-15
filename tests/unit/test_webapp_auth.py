"""Telegram Mini App HMAC, freshness and replay-protection tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from app.api.telegram_auth import (
    RedisReplayStore,
    ReplayStoreError,
    TelegramInitDataVerifier,
    WebAppAuthenticationError,
)

TOKEN = "123456:production-shaped-secret"
NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def signed_init_data(
    *,
    token: str = TOKEN,
    auth_date: datetime = NOW,
    user_id: int = 123_456_789,
    extra: dict[str, str] | None = None,
) -> str:
    fields = {
        "auth_date": str(int(auth_date.timestamp())),
        "query_id": "AAEAA-safe-query",
        "signature": "third-party-signature-is-part-of-bot-token-check",
        "user": json.dumps(
            {"id": user_id, "first_name": "Client", "username": "private"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        **(extra or {}),
    }
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.digest(b"WebAppData", token.encode(), "sha256")
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(list(reversed(fields.items())))


class MemoryReplayStore:
    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.calls: list[tuple[str, int]] = []

    async def claim(self, fingerprint: str, *, ttl_seconds: int) -> bool:
        self.calls.append((fingerprint, ttl_seconds))
        if fingerprint in self.claimed:
            return False
        self.claimed.add(fingerprint)
        return True


def verifier(store: MemoryReplayStore | None = None) -> TelegramInitDataVerifier:
    return TelegramInitDataVerifier(
        SecretStr(TOKEN),
        store or MemoryReplayStore(),
        max_age_seconds=300,
    )


@pytest.mark.asyncio
async def test_fixed_bot_token_hmac_vector_includes_signature_field() -> None:
    raw = (
        "auth_date=1786363200&query_id=AAEAA-safe-query&"
        "signature=third-party-signature-is-part-of-bot-token-check&"
        "user=%7B%22id%22%3A123456789%2C%22first_name%22%3A%22Client%22%2C"
        "%22username%22%3A%22private%22%7D&"
        "hash=7b0ab45e3b1aef9cdd6ef063faeb6d2d2c4a1783c2e1e4ef0a630ce1668ffe98"
    )

    identity = await verifier().verify_and_claim(raw, now=NOW)

    assert identity.telegram_user_id == 123_456_789


@pytest.mark.asyncio
async def test_valid_init_data_exposes_only_identity_after_hmac_and_replay_claim() -> None:
    store = MemoryReplayStore()
    auth = verifier(store)
    raw = signed_init_data(extra={"start_param": "avito_campaign-1"})

    identity = await auth.verify_and_claim(raw, now=NOW)

    assert identity.telegram_user_id == 123_456_789
    assert identity.auth_date == NOW
    assert identity.start_parameter == "avito_campaign-1"
    assert len(identity.session_fingerprint) == 64
    assert store.calls == [(identity.session_fingerprint, 300)]
    assert "Client" not in repr(identity)
    assert TOKEN not in repr(auth)


@pytest.mark.asyncio
async def test_tampering_is_rejected_before_replay_storage() -> None:
    store = MemoryReplayStore()
    auth = verifier(store)
    raw = signed_init_data().replace("123456789", "123456788")

    with pytest.raises(WebAppAuthenticationError, match="webapp_authentication_failed"):
        await auth.verify_and_claim(raw, now=NOW)

    assert store.calls == []


@pytest.mark.asyncio
async def test_expired_and_far_future_auth_dates_are_rejected() -> None:
    auth = verifier()

    with pytest.raises(WebAppAuthenticationError, match="webapp_init_data_expired"):
        await auth.verify_and_claim(
            signed_init_data(auth_date=NOW - timedelta(seconds=301)),
            now=NOW,
        )
    with pytest.raises(WebAppAuthenticationError, match="webapp_auth_date_in_future"):
        await auth.verify_and_claim(
            signed_init_data(auth_date=NOW + timedelta(seconds=31)),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_same_init_data_exchange_is_single_use() -> None:
    auth = verifier()
    raw = signed_init_data()

    await auth.verify_and_claim(raw, now=NOW)
    with pytest.raises(WebAppAuthenticationError, match="webapp_init_data_replayed"):
        await auth.verify_and_claim(raw, now=NOW)


@pytest.mark.asyncio
async def test_duplicate_query_fields_are_rejected_even_when_hash_is_present() -> None:
    store = MemoryReplayStore()
    auth = verifier(store)
    raw = signed_init_data()

    with pytest.raises(WebAppAuthenticationError):
        await auth.verify_and_claim(f"{raw}&auth_date=1", now=NOW)

    assert store.calls == []


@pytest.mark.asyncio
async def test_signed_payload_without_valid_user_id_is_rejected() -> None:
    auth = verifier()

    with pytest.raises(WebAppAuthenticationError):
        await auth.verify_and_claim(signed_init_data(user_id=0), now=NOW)


class FakeRedis:
    def __init__(self, result: object = True) -> None:
        self.result = result
        self.calls: list[tuple[str, str, int, bool]] = []

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> object:
        if isinstance(self.result, Exception):
            raise self.result
        self.calls.append((name, value, ex, nx))
        return self.result


@pytest.mark.asyncio
async def test_redis_replay_store_uses_atomic_nx_and_only_digest_key() -> None:
    redis = FakeRedis()
    store = RedisReplayStore(redis, namespace="tenant_a")
    fingerprint = "a" * 64

    assert await store.claim(fingerprint, ttl_seconds=120)
    assert redis.calls == [(f"tenant_a:webapp_replay:{fingerprint}", "1", 120, True)]


@pytest.mark.asyncio
async def test_redis_replay_store_fails_closed_without_leaking_backend_error() -> None:
    redis = FakeRedis(OSError("redis://user:secret@example.test"))
    store = RedisReplayStore(redis)

    with pytest.raises(ReplayStoreError) as error:
        await store.claim("a" * 64, ttl_seconds=120)

    assert str(error.value) == "replay store unavailable"
    assert "secret" not in str(error.value)
