"""Lease-based reliable delivery for waitlist window matches."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.database.models import WaitlistNotification
from app.domain.enums import (
    AvailabilityWindowStatus,
    WaitlistNotificationStatus,
    WaitlistStatus,
)
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.features import FeatureName
from app.schemas.waitlist import WaitlistDelivery
from app.services.feature_guard import is_feature_enabled

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class WaitlistDeliveryService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        lease_seconds: int,
        max_attempts: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def claim_due(
        self,
        worker_id: str,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> list[int]:
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as uow:
            jobs = await uow.waitlist.claim_due_notifications(
                now=current,
                lease_expired_before=current - timedelta(seconds=self._lease_seconds),
                worker_id=worker_id,
                limit=limit,
            )
            ids = [job.id for job in jobs]
            await uow.commit()
            return ids

    async def prepare_delivery(
        self,
        notification_id: int,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> WaitlistDelivery | None:
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as uow:
            job = await self._claimed(uow, notification_id, worker_id)
            if job is None:
                return None
            if not await is_feature_enabled(uow, FeatureName.WAITLIST):
                await self._finish(
                    uow,
                    job,
                    WaitlistNotificationStatus.CANCELLED,
                    "feature_disabled",
                )
                await uow.commit()
                return None
            entry = await uow.waitlist.get(job.waitlist_entry_id)
            window = await uow.windows.get(job.window_id)
            if (
                entry is None
                or entry.status not in {WaitlistStatus.ACTIVE, WaitlistStatus.MATCHED}
                or entry.expires_at <= current
                or window is None
                or window.status is not AvailabilityWindowStatus.OPEN
                or window.start_at <= current
            ):
                await self._finish(uow, job, WaitlistNotificationStatus.CANCELLED, "match_inactive")
                await uow.commit()
                return None
            recipient = await uow.users.get_by_id(entry.client_id)
            service = await uow.services.get(entry.service_id)
            settings = await uow.settings.get()
            if recipient is None or service is None or settings is None:
                await self._finish(uow, job, WaitlistNotificationStatus.FAILED, "context_missing")
                await uow.commit()
                return None
            if recipient.is_blocked:
                await self._finish(uow, job, WaitlistNotificationStatus.FAILED, "recipient_blocked")
                await uow.commit()
                return None
            return WaitlistDelivery(
                notification_id=job.id,
                entry_id=entry.id,
                window_id=window.id,
                recipient_user_id=recipient.id,
                recipient_telegram_id=recipient.telegram_id,
                service_name=service.name,
                start_at=window.start_at,
                timezone=settings.timezone,
                attempts=job.attempts,
            )

    async def mark_sent(self, notification_id: int, worker_id: str) -> bool:
        async with self._unit_of_work_factory() as uow:
            job = await self._claimed(uow, notification_id, worker_id)
            if job is None:
                return False
            await self._finish(uow, job, WaitlistNotificationStatus.SENT, None)
            job.sent_at = datetime.now(UTC)
            await uow.commit()
            return True

    async def retry(
        self,
        notification_id: int,
        worker_id: str,
        *,
        delay_seconds: int,
        error_code: str,
    ) -> bool:
        async with self._unit_of_work_factory() as uow:
            job = await self._claimed(uow, notification_id, worker_id)
            if job is None:
                return False
            if job.attempts >= self._max_attempts:
                await self._finish(
                    uow, job, WaitlistNotificationStatus.FAILED, "attempts_exhausted"
                )
            else:
                job.status = WaitlistNotificationStatus.RETRY
                job.available_at = datetime.now(UTC) + timedelta(seconds=max(1, delay_seconds))
                job.locked_at = None
                job.locked_by = None
                job.last_error = error_code[:1000]
            await uow.commit()
            return True

    async def mark_recipient_blocked(self, notification_id: int, worker_id: str) -> bool:
        async with self._unit_of_work_factory() as uow:
            job = await self._claimed(uow, notification_id, worker_id)
            if job is None:
                return False
            entry = await uow.waitlist.get(job.waitlist_entry_id)
            if entry is not None:
                recipient = await uow.users.get_by_id(entry.client_id)
                if recipient is not None:
                    await uow.users.mark_blocked(recipient)
            await self._finish(uow, job, WaitlistNotificationStatus.FAILED, "telegram_forbidden")
            await uow.commit()
            return True

    async def mark_permanent_failure(
        self, notification_id: int, worker_id: str, *, error_code: str
    ) -> bool:
        async with self._unit_of_work_factory() as uow:
            job = await self._claimed(uow, notification_id, worker_id)
            if job is None:
                return False
            await self._finish(uow, job, WaitlistNotificationStatus.FAILED, error_code)
            await uow.commit()
            return True

    @staticmethod
    async def _claimed(
        uow: SqlAlchemyUnitOfWork, notification_id: int, worker_id: str
    ) -> WaitlistNotification | None:
        job = await uow.waitlist.get_notification(notification_id, for_update=True)
        if (
            job is None
            or job.status is not WaitlistNotificationStatus.PROCESSING
            or job.locked_by != worker_id
        ):
            return None
        return job

    @staticmethod
    async def _finish(
        uow: SqlAlchemyUnitOfWork,
        job: WaitlistNotification,
        status: WaitlistNotificationStatus,
        error: str | None,
    ) -> None:
        job.status = status
        job.locked_at = None
        job.locked_by = None
        job.last_error = error[:1000] if error is not None else None
        await uow.session.flush()

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
