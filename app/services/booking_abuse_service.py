"""Layered booking-abuse controls backed by Redis and transaction-scoped DB quotas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.errors import BookingUnavailableError
from app.domain.payments import aware_utc
from app.security.rate_limit import RateLimitDecision, RateLimiterError, RedisRateLimiter


class BookingAbuseError(BookingUnavailableError):
    """A safe, transport-independent rejection with a stable machine code."""

    def __init__(self, message: str, *, code: str, retry_after_seconds: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


class BookingQuotaRepository(Protocol):
    async def lock_client_for_booking(self, client_id: int) -> object | None: ...

    async def count_future_appointments(self, *, client_id: int, now: datetime) -> int: ...

    async def count_client_appointments_between(
        self, *, client_id: int, start_at: datetime, end_at: datetime
    ) -> int: ...

    async def count_active_reservations(self, *, client_id: int, now: datetime) -> int: ...


class BookingRateLimiter(Protocol):
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

    async def inspect(
        self,
        scope: str,
        *,
        business_id: int,
        subject_id: int,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision: ...

    async def reset(self, scope: str, *, business_id: int, subject_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class BookingAbusePolicy:
    max_future_appointments: int = 4
    max_active_reservations: int = 2
    max_appointments_per_day: int = 2
    attempt_limit: int = 6
    attempt_window_seconds: int = 60
    cooldown_seconds: int = 3
    failed_attempt_limit: int = 5
    failed_attempt_window_seconds: int = 900

    def __post_init__(self) -> None:
        values = (
            self.max_future_appointments,
            self.max_active_reservations,
            self.max_appointments_per_day,
            self.attempt_limit,
            self.attempt_window_seconds,
            self.cooldown_seconds,
            self.failed_attempt_limit,
            self.failed_attempt_window_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("booking abuse policy values must be positive")
        if self.attempt_window_seconds > 86_400 or self.failed_attempt_window_seconds > 86_400:
            raise ValueError("booking abuse windows cannot exceed one day")


class BookingAbusePolicyService:
    """Fail closed on limiter outages and serialize durable quota checks per client."""

    def __init__(
        self,
        repository: BookingQuotaRepository,
        limiter: BookingRateLimiter,
        *,
        business_id: int,
        policy: BookingAbusePolicy | None = None,
    ) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self._repository = repository
        self._limiter = limiter
        self._business_id = business_id
        self._policy = policy or BookingAbusePolicy()

    async def authorize_new_reservation(
        self,
        *,
        client_id: int,
        request_id: str,
        day_start_at: datetime,
        day_end_at: datetime,
        now: datetime | None = None,
    ) -> None:
        """Authorize inside the same transaction that will create the reservation."""

        current = aware_utc(now)
        day_start = aware_utc(day_start_at)
        day_end = aware_utc(day_end_at)
        if client_id <= 0:
            raise ValueError("client_id must be positive")
        if not day_start <= current < day_end:
            raise ValueError("current time must be inside the supplied business day")

        await self._enforce_redis_limits(client_id, request_id)

        client = await self._repository.lock_client_for_booking(client_id)
        if client is None:
            raise BookingAbuseError(
                "Client is not active in this business.", code="inactive_business_client"
            )
        future = await self._repository.count_future_appointments(client_id=client_id, now=current)
        if future >= self._policy.max_future_appointments:
            raise BookingAbuseError(
                "Too many future appointments.", code="future_appointment_limit"
            )
        active = await self._repository.count_active_reservations(client_id=client_id, now=current)
        if active >= self._policy.max_active_reservations:
            raise BookingAbuseError(
                "Too many active reservations.", code="active_reservation_limit"
            )
        daily = await self._repository.count_client_appointments_between(
            client_id=client_id,
            start_at=day_start,
            end_at=day_end,
        )
        if daily >= self._policy.max_appointments_per_day:
            raise BookingAbuseError(
                "Daily appointment limit reached.", code="daily_appointment_limit"
            )

    async def record_failed_attempt(self, *, client_id: int, failure_id: str) -> None:
        """Record only a safe correlation/idempotency id, never Telegram payload data."""

        try:
            await self._limiter.consume(
                "booking_failures",
                business_id=self._business_id,
                subject_id=client_id,
                request_id=failure_id,
                limit=self._policy.failed_attempt_limit,
                window_seconds=self._policy.failed_attempt_window_seconds,
            )
        except RateLimiterError as exc:
            raise self._limiter_unavailable() from exc

    async def clear_failed_attempts(self, *, client_id: int) -> None:
        try:
            await self._limiter.reset(
                "booking_failures",
                business_id=self._business_id,
                subject_id=client_id,
            )
        except RateLimiterError as exc:
            raise self._limiter_unavailable() from exc

    async def _enforce_redis_limits(self, client_id: int, request_id: str) -> None:
        try:
            failures = await self._limiter.inspect(
                "booking_failures",
                business_id=self._business_id,
                subject_id=client_id,
                limit=self._policy.failed_attempt_limit,
                window_seconds=self._policy.failed_attempt_window_seconds,
            )
            self._require_allowed(failures, "too_many_failed_attempts")
            attempts = await self._limiter.consume(
                "booking_attempts",
                business_id=self._business_id,
                subject_id=client_id,
                request_id=request_id,
                limit=self._policy.attempt_limit,
                window_seconds=self._policy.attempt_window_seconds,
            )
            self._require_allowed(attempts, "booking_rate_limit")
            cooldown = await self._limiter.consume(
                "booking_cooldown",
                business_id=self._business_id,
                subject_id=client_id,
                request_id=request_id,
                limit=1,
                window_seconds=self._policy.cooldown_seconds,
            )
            self._require_allowed(cooldown, "booking_cooldown")
        except RateLimiterError as exc:
            raise self._limiter_unavailable() from exc

    @staticmethod
    def _require_allowed(decision: RateLimitDecision, code: str) -> None:
        if not decision.allowed:
            raise BookingAbuseError(
                "Please wait before trying to book again.",
                code=code,
                retry_after_seconds=decision.retry_after_seconds,
            )

    @staticmethod
    def _limiter_unavailable() -> BookingAbuseError:
        return BookingAbuseError(
            "Booking protection is temporarily unavailable.",
            code="booking_protection_unavailable",
            retry_after_seconds=5,
        )


def default_booking_abuse_service(
    repository: BookingQuotaRepository,
    redis: RedisRateLimiter,
    *,
    business_id: int,
) -> BookingAbusePolicyService:
    """Typed composition helper for the production Redis implementation."""

    return BookingAbusePolicyService(repository, redis, business_id=business_id)
