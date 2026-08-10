"""CRM subscription access rules, independent of client service payments."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.domain.enums import SubscriptionStatus
from app.domain.errors import DomainError, EntityNotFoundError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.subscription import SubscriptionView
from app.subscriptions.providers import SubscriptionStatusProvider

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class SubscriptionAccessError(DomainError):
    """New business activity is unavailable after the subscription grace period."""


class DatabaseSubscriptionStatusProvider:
    """MVP provider backed by persisted state; replaceable by future external billing."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def get_status(self, business_id: int) -> SubscriptionView:
        async with self._unit_of_work_factory() as unit_of_work:
            if business_id != unit_of_work.business_id:
                raise EntityNotFoundError("Subscription was not found")
            subscription = await unit_of_work.subscriptions.get()
            if subscription is None:
                raise EntityNotFoundError("Subscription was not found")
            return SubscriptionView(
                business_id=subscription.business_id,
                plan_code=subscription.plan_code,
                provider=subscription.provider,
                status=subscription.status,
                paid_until=subscription.current_period_ends_at,
                grace_ends_at=subscription.grace_ends_at,
                next_payment_at=subscription.next_payment_at,
                blocking_reason_code=subscription.blocking_reason_code,
                feature_limits=dict(subscription.feature_limits),
            )


class SubscriptionService:
    """Apply grace-period rules without deleting or hiding existing business data."""

    def __init__(self, provider: SubscriptionStatusProvider) -> None:
        self._provider = provider

    async def get_status(self, business_id: int) -> SubscriptionView:
        return await self._provider.get_status(business_id)

    async def ensure_new_bookings_allowed(
        self,
        business_id: int,
        *,
        now: datetime | None = None,
    ) -> SubscriptionView:
        current = self._aware_now(now)
        status = await self.get_status(business_id)
        if not self._allows_new_booking(status, current):
            raise SubscriptionAccessError("New bookings are unavailable for this business")
        return status

    @classmethod
    def owner_warning_due(
        cls,
        status: SubscriptionView,
        *,
        now: datetime | None = None,
        warning_days: int = 7,
    ) -> bool:
        if not 1 <= warning_days <= 30:
            raise ValueError("warning_days must be between 1 and 30")
        current = cls._aware_now(now)
        boundary = status.paid_until or status.grace_ends_at
        return bool(
            status.status is SubscriptionStatus.PAST_DUE
            or (
                boundary is not None
                and current <= boundary <= current + timedelta(days=warning_days)
            )
        )

    @staticmethod
    def _allows_new_booking(status: SubscriptionView, now: datetime) -> bool:
        if status.status in {SubscriptionStatus.SUSPENDED, SubscriptionStatus.CANCELLED}:
            return False
        if status.status is SubscriptionStatus.PAST_DUE:
            return status.grace_ends_at is not None and now <= status.grace_ends_at
        if status.paid_until is not None and now > status.paid_until:
            return status.grace_ends_at is not None and now <= status.grace_ends_at
        return True

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
