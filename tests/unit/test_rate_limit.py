"""Atomic Redis rate-limit behavior and safe key construction."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from app.security.rate_limit import RateLimiterError, RedisRateLimiter


class FakeRedis:
    def __init__(self, responses: Iterable[object]) -> None:
        self.responses = iter(responses)
        self.eval_calls: list[tuple[str, int, tuple[str | int, ...]]] = []
        self.deleted: list[str] = []

    async def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> object:
        self.eval_calls.append((script, numkeys, keys_and_args))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result

    async def delete(self, *names: str) -> int:
        self.deleted.extend(names)
        return len(names)


@pytest.mark.asyncio
async def test_consume_is_one_atomic_lua_call_and_reuses_safe_business_key() -> None:
    redis = FakeRedis([[1, 4, 0]])
    limiter = RedisRateLimiter(redis)

    decision = await limiter.consume(
        "booking_attempts",
        business_id=7,
        subject_id=41,
        request_id="request-12345678",
        limit=5,
        window_seconds=60,
    )

    assert decision.allowed
    assert decision.remaining == 4
    assert len(redis.eval_calls) == 1
    script, numkeys, args = redis.eval_calls[0]
    assert numkeys == 1
    assert args == ("lanrouge:rate:7:booking_attempts:41", 60_000, 5, "request-12345678")
    assert "redis.call('TIME')" in script
    assert "ZREMRANGEBYSCORE" in script
    assert "ZSCORE" in script
    assert "PEXPIRE" in script


@pytest.mark.asyncio
async def test_denied_retry_is_rounded_up_to_seconds() -> None:
    redis = FakeRedis([[0, 0, 1_001]])
    limiter = RedisRateLimiter(redis)

    decision = await limiter.inspect(
        "booking_failures",
        business_id=7,
        subject_id=41,
        limit=5,
        window_seconds=900,
    )

    assert not decision.allowed
    assert decision.retry_after_seconds == 2


@pytest.mark.asyncio
async def test_redis_failure_is_wrapped_without_connection_details() -> None:
    redis = FakeRedis([OSError("redis://user:super-secret@example")])
    limiter = RedisRateLimiter(redis)

    with pytest.raises(RateLimiterError, match="rate limiter unavailable") as error:
        await limiter.consume(
            "booking_attempts",
            business_id=7,
            subject_id=41,
            request_id="request-12345678",
            limit=5,
            window_seconds=60,
        )

    assert "super-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_invalid_scope_never_reaches_redis() -> None:
    redis = FakeRedis([])
    limiter = RedisRateLimiter(redis)

    with pytest.raises(ValueError, match="scope"):
        await limiter.inspect(
            "../foreign:key",
            business_id=7,
            subject_id=41,
            limit=5,
            window_seconds=60,
        )

    assert redis.eval_calls == []


@pytest.mark.asyncio
async def test_reset_uses_the_same_scoped_key() -> None:
    redis = FakeRedis([])
    limiter = RedisRateLimiter(redis, namespace="nails")

    await limiter.reset("booking_failures", business_id=7, subject_id=41)

    assert redis.deleted == ["nails:rate:7:booking_failures:41"]
