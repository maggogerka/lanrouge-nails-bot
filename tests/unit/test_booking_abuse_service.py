"""Booking abuse policy combines fail-closed Redis checks with locked DB quotas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.security.rate_limit import RateLimitDecision, RateLimiterError
from app.services.booking_abuse_service import (
    BookingAbuseError,
    BookingAbusePolicyService,
)

NOW = datetime(2026, 8, 10, 10, tzinfo=UTC)
DAY_START = datetime(2026, 8, 10, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)


def build_service() -> tuple[BookingAbusePolicyService, MagicMock, MagicMock]:
    repository = MagicMock()
    repository.lock_client_for_booking = AsyncMock(return_value=SimpleNamespace(id=9))
    repository.count_future_appointments = AsyncMock(return_value=0)
    repository.count_active_reservations = AsyncMock(return_value=0)
    repository.count_client_appointments_between = AsyncMock(return_value=0)
    limiter = MagicMock()
    limiter.inspect = AsyncMock(return_value=RateLimitDecision(True, 5, 0))
    limiter.consume = AsyncMock(return_value=RateLimitDecision(True, 4, 0))
    limiter.reset = AsyncMock()
    service = BookingAbusePolicyService(repository, limiter, business_id=7)
    return service, repository, limiter


@pytest.mark.asyncio
async def test_authorization_enforces_redis_then_locks_client_and_counts_quotas() -> None:
    service, repository, limiter = build_service()

    await service.authorize_new_reservation(
        client_id=41,
        request_id="request-12345678",
        day_start_at=DAY_START,
        day_end_at=DAY_END,
        now=NOW,
    )

    limiter.inspect.assert_awaited_once()
    assert limiter.consume.await_count == 2
    repository.lock_client_for_booking.assert_awaited_once_with(41)
    repository.count_future_appointments.assert_awaited_once_with(client_id=41, now=NOW)
    repository.count_active_reservations.assert_awaited_once_with(client_id=41, now=NOW)


@pytest.mark.asyncio
async def test_locked_future_quota_rejects_before_reservation_count() -> None:
    service, repository, _ = build_service()
    repository.count_future_appointments.return_value = 3

    with pytest.raises(BookingAbuseError) as error:
        await service.authorize_new_reservation(
            client_id=41,
            request_id="request-12345678",
            day_start_at=DAY_START,
            day_end_at=DAY_END,
            now=NOW,
        )

    assert error.value.code == "future_appointment_limit"
    repository.count_active_reservations.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_attempt_lockout_does_not_query_database() -> None:
    service, repository, limiter = build_service()
    limiter.inspect.return_value = RateLimitDecision(False, 0, 125)

    with pytest.raises(BookingAbuseError) as error:
        await service.authorize_new_reservation(
            client_id=41,
            request_id="request-12345678",
            day_start_at=DAY_START,
            day_end_at=DAY_END,
            now=NOW,
        )

    assert error.value.code == "too_many_failed_attempts"
    assert error.value.retry_after_seconds == 125
    repository.lock_client_for_booking.assert_not_awaited()


@pytest.mark.asyncio
async def test_limiter_outage_fails_closed_with_safe_error() -> None:
    service, repository, limiter = build_service()
    limiter.inspect.side_effect = RateLimiterError("secret backend detail")

    with pytest.raises(BookingAbuseError) as error:
        await service.authorize_new_reservation(
            client_id=41,
            request_id="request-12345678",
            day_start_at=DAY_START,
            day_end_at=DAY_END,
            now=NOW,
        )

    assert error.value.code == "booking_protection_unavailable"
    assert "secret" not in str(error.value)
    repository.lock_client_for_booking.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_recording_and_success_reset_are_business_scoped() -> None:
    service, _, limiter = build_service()

    await service.record_failed_attempt(client_id=41, failure_id="failure-12345678")
    await service.clear_failed_attempts(client_id=41)

    limiter.consume.assert_awaited_once_with(
        "booking_failures",
        business_id=7,
        subject_id=41,
        request_id="failure-12345678",
        limit=5,
        window_seconds=900,
    )
    limiter.reset.assert_awaited_once_with("booking_failures", business_id=7, subject_id=41)
