"""Atomic Redis sliding-window limits without storing Telegram identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")

_CONSUME_SCRIPT = """
local now_parts = redis.call('TIME')
local now_ms = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]
local cutoff = now_ms - window_ms
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
local existing = redis.call('ZSCORE', KEYS[1], member)
local count = redis.call('ZCARD', KEYS[1])
if existing then
    redis.call('PEXPIRE', KEYS[1], window_ms)
    return {1, math.max(limit - count, 0), 0}
end
if count >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry_ms = window_ms
    if oldest[2] then
        retry_ms = math.max(1, math.ceil(tonumber(oldest[2]) + window_ms - now_ms))
    end
    redis.call('PEXPIRE', KEYS[1], window_ms)
    return {0, 0, retry_ms}
end
redis.call('ZADD', KEYS[1], now_ms, member)
count = count + 1
redis.call('PEXPIRE', KEYS[1], window_ms)
return {1, math.max(limit - count, 0), 0}
"""

_INSPECT_SCRIPT = """
local now_parts = redis.call('TIME')
local now_ms = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local cutoff = now_ms - window_ms
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry_ms = window_ms
    if oldest[2] then
        retry_ms = math.max(1, math.ceil(tonumber(oldest[2]) + window_ms - now_ms))
    end
    redis.call('PEXPIRE', KEYS[1], window_ms)
    return {0, 0, retry_ms}
end
if count == 0 then
    redis.call('DEL', KEYS[1])
else
    redis.call('PEXPIRE', KEYS[1], window_ms)
end
return {1, math.max(limit - count, 0), 0}
"""


class RedisEvalClient(Protocol):
    """Minimal redis-py compatible async surface used by the limiter."""

    async def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> object: ...

    async def delete(self, *names: str) -> int: ...


class RateLimiterError(RuntimeError):
    """Redis returned an invalid response or could not enforce a limit."""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RedisRateLimiter:
    """Server-time sliding windows executed atomically in one Redis Lua call."""

    def __init__(self, redis: RedisEvalClient, *, namespace: str = "lanrouge") -> None:
        if not _SAFE_NAME.fullmatch(namespace):
            raise ValueError("namespace must be a safe lowercase identifier")
        self._redis = redis
        self._namespace = namespace

    async def consume(
        self,
        scope: str,
        *,
        business_id: int,
        subject_id: int,
        request_id: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        """Consume once; replaying the same request id does not consume twice."""

        key = self._key(scope, business_id, subject_id)
        self._validate_window(limit, window_seconds)
        if not _SAFE_REQUEST_ID.fullmatch(request_id):
            raise ValueError("request_id must contain 8-128 safe ASCII characters")
        try:
            raw = await self._redis.eval(
                _CONSUME_SCRIPT,
                1,
                key,
                window_seconds * 1000,
                limit,
                request_id,
            )
        except Exception as exc:
            raise RateLimiterError("rate limiter unavailable") from exc
        return self._decision(raw)

    async def inspect(
        self,
        scope: str,
        *,
        business_id: int,
        subject_id: int,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        """Read a cleaned-up window atomically without consuming capacity."""

        key = self._key(scope, business_id, subject_id)
        self._validate_window(limit, window_seconds)
        try:
            raw = await self._redis.eval(
                _INSPECT_SCRIPT,
                1,
                key,
                window_seconds * 1000,
                limit,
            )
        except Exception as exc:
            raise RateLimiterError("rate limiter unavailable") from exc
        return self._decision(raw)

    async def reset(self, scope: str, *, business_id: int, subject_id: int) -> None:
        key = self._key(scope, business_id, subject_id)
        try:
            await self._redis.delete(key)
        except Exception as exc:
            raise RateLimiterError("rate limiter unavailable") from exc

    def _key(self, scope: str, business_id: int, subject_id: int) -> str:
        if business_id <= 0 or subject_id <= 0:
            raise ValueError("business_id and subject_id must be positive")
        if not _SAFE_NAME.fullmatch(scope):
            raise ValueError("scope must be a safe lowercase identifier")
        return f"{self._namespace}:rate:{business_id}:{scope}:{subject_id}"

    @staticmethod
    def _validate_window(limit: int, window_seconds: int) -> None:
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        if not 1 <= window_seconds <= 86_400:
            raise ValueError("window_seconds must be between 1 and 86400")

    @staticmethod
    def _decision(raw: object) -> RateLimitDecision:
        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise RateLimiterError("invalid rate limiter response")
        try:
            allowed, remaining, retry_ms = (int(item) for item in raw)
        except (TypeError, ValueError) as exc:
            raise RateLimiterError("invalid rate limiter response") from exc
        if allowed not in {0, 1} or remaining < 0 or retry_ms < 0:
            raise RateLimiterError("invalid rate limiter response")
        retry_seconds = (retry_ms + 999) // 1000
        return RateLimitDecision(bool(allowed), remaining, retry_seconds)
