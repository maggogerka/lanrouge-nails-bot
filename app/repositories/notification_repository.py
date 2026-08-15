"""Persistence for the durable notification queue."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import NotificationJob
from app.domain.enums import NotificationJobStatus, NotificationType
from app.repositories.scoped import TenantScopedRepository


class NotificationRepository(TenantScopedRepository):
    """Write notification jobs in their owning appointment transaction."""

    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def add_all(self, jobs: list[NotificationJob]) -> None:
        for job in jobs:
            self._require_business(job.business_id)
        self._session.add_all(jobs)
        await self._session.flush()

    async def get(
        self,
        job_id: int,
        *,
        for_update: bool = False,
    ) -> NotificationJob | None:
        statement = select(NotificationJob).where(
            NotificationJob.id == job_id,
            NotificationJob.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def claim_due(
        self,
        *,
        now: datetime,
        lease_expired_before: datetime,
        worker_id: str,
        limit: int,
    ) -> list[NotificationJob]:
        """Claim pending jobs and expired processing leases without worker contention."""

        result = await self._session.scalars(
            select(NotificationJob)
            .where(
                NotificationJob.business_id == self.business_id,
                or_(
                    and_(
                        NotificationJob.status == NotificationJobStatus.PENDING,
                        NotificationJob.available_at <= now,
                    ),
                    and_(
                        NotificationJob.status == NotificationJobStatus.PROCESSING,
                        NotificationJob.available_at <= now,
                        NotificationJob.locked_at <= lease_expired_before,
                    ),
                ),
            )
            .order_by(NotificationJob.available_at, NotificationJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(result.all())
        for job in jobs:
            job.status = NotificationJobStatus.PROCESSING
            job.attempts += 1
            job.locked_at = now
            job.locked_by = worker_id
        await self._session.flush()
        return jobs

    async def list_for_appointment(self, appointment_id: int) -> list[NotificationJob]:
        result = await self._session.scalars(
            select(NotificationJob).where(
                NotificationJob.business_id == self.business_id,
                NotificationJob.appointment_id == appointment_id,
            )
        )
        return list(result.all())

    async def cancel_unsent(self, appointment_id: int) -> int:
        result = await self._session.execute(
            update(NotificationJob)
            .where(
                NotificationJob.business_id == self.business_id,
                NotificationJob.appointment_id == appointment_id,
                NotificationJob.status.in_(
                    (NotificationJobStatus.PENDING, NotificationJobStatus.PROCESSING)
                ),
            )
            .values(
                status=NotificationJobStatus.CANCELLED,
                locked_at=None,
                locked_by=None,
            )
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def cancel_unsent_by_type(self, notification_type: NotificationType) -> int:
        result = await self._session.execute(
            update(NotificationJob)
            .where(
                NotificationJob.business_id == self.business_id,
                NotificationJob.notification_type == notification_type,
                NotificationJob.status.in_(
                    (NotificationJobStatus.PENDING, NotificationJobStatus.PROCESSING)
                ),
            )
            .values(status=NotificationJobStatus.CANCELLED, locked_at=None, locked_by=None)
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)
