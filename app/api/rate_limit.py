"""HTTP adapter over the shared Redis sliding-window limiter."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from app.security.rate_limit import RateLimitDecision, RateLimiterError


class SharedRateLimiter(Protocol):
    async def consume(
        self,
        scope: str,
        *,
        business_id: int,
        subject_id: int,
        request_id: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision: ...


class HttpRateLimitError(RuntimeError):
    """Safe rate-limit failure used by the HTTP boundary."""

    def __init__(self, code: str, *, retry_after_seconds: int) -> None:
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class HttpRateLimitPolicy:
    scope: str
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if not self.scope or len(self.scope) > 48:
            raise ValueError("HTTP rate-limit scope must contain 1..48 characters")
        if not 1 <= self.limit <= 100_000:
            raise ValueError("HTTP rate limit must be between 1 and 100000")
        if not 1 <= self.window_seconds <= 86_400:
            raise ValueError("HTTP rate-limit window must be between 1 and 86400 seconds")


class SharedHttpRateLimiter:
    """Hash network subjects before delegating to the existing Redis limiter."""

    def __init__(
        self,
        limiter: SharedRateLimiter,
        subject_key: SecretStr,
        *,
        business_id: int,
    ) -> None:
        raw_key = subject_key.get_secret_value()
        if not raw_key:
            raise ValueError("HTTP rate-limit subject key is required")
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self._limiter = limiter
        self._subject_key = hashlib.sha256(raw_key.encode("utf-8")).digest()
        self._business_id = business_id

    async def enforce(
        self,
        *,
        subject: str,
        request_id: str,
        policy: HttpRateLimitPolicy,
    ) -> None:
        if not subject or len(subject) > 256:
            subject = "unknown"
        digest = hmac.digest(self._subject_key, subject.encode("utf-8"), "sha256")
        opaque_subject_id = (int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)) or 1
        try:
            decision = await self._limiter.consume(
                policy.scope,
                business_id=self._business_id,
                subject_id=opaque_subject_id,
                request_id=request_id,
                limit=policy.limit,
                window_seconds=policy.window_seconds,
            )
        except RateLimiterError:
            raise HttpRateLimitError(
                "rate_limiter_unavailable",
                retry_after_seconds=5,
            ) from None
        if not decision.allowed:
            raise HttpRateLimitError(
                "rate_limit_exceeded",
                retry_after_seconds=max(1, decision.retry_after_seconds),
            )
