"""HTTP subject-pseudonymization adapter tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from app.api.rate_limit import (
    HttpRateLimitError,
    HttpRateLimitPolicy,
    SharedHttpRateLimiter,
)
from app.security.rate_limit import RateLimitDecision, RateLimiterError


@pytest.mark.asyncio
async def test_http_rate_limit_hashes_network_subject_before_shared_limiter() -> None:
    limiter = MagicMock()
    limiter.consume = AsyncMock(return_value=RateLimitDecision(True, 9, 0))
    adapter = SharedHttpRateLimiter(
        limiter,
        SecretStr("dedicated-rate-key"),
        business_id=7,
    )

    await adapter.enforce(
        subject="203.0.113.42",
        request_id="request-12345678",
        policy=HttpRateLimitPolicy("miniapp_auth", 10, 60),
    )

    call = limiter.consume.await_args
    assert call.args == ("miniapp_auth",)
    assert call.kwargs["business_id"] == 7
    assert isinstance(call.kwargs["subject_id"], int)
    assert str(call.kwargs["subject_id"]) != "203.0.113.42"


@pytest.mark.asyncio
async def test_http_rate_limit_rejection_and_outage_are_safe() -> None:
    limiter = MagicMock()
    limiter.consume = AsyncMock(return_value=RateLimitDecision(False, 0, 17))
    adapter = SharedHttpRateLimiter(limiter, SecretStr("rate-key"), business_id=7)
    policy = HttpRateLimitPolicy("miniapp_auth", 10, 60)

    with pytest.raises(HttpRateLimitError) as rejected:
        await adapter.enforce(
            subject="203.0.113.42",
            request_id="request-12345678",
            policy=policy,
        )
    assert rejected.value.code == "rate_limit_exceeded"
    assert rejected.value.retry_after_seconds == 17

    limiter.consume.side_effect = RateLimiterError("redis://user:secret@example")
    with pytest.raises(HttpRateLimitError) as unavailable:
        await adapter.enforce(
            subject="203.0.113.42",
            request_id="request-87654321",
            policy=policy,
        )
    assert unavailable.value.code == "rate_limiter_unavailable"
    assert "secret" not in str(unavailable.value)
