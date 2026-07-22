"""Persistence and concurrent claims for waitlist requests and notifications."""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Service, WaitlistEntry, WaitlistNotification
from app.domain.enums import WaitlistNotificationStatus, WaitlistStatus


class WaitlistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entry_id: int, *, for_update: bool = False) -> WaitlistEntry | None:
        statement = select(WaitlistEntry).where(WaitlistEntry.id == entry_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def add(self, entry: WaitlistEntry) -> WaitlistEntry:
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_for_client(
        self,
        client_id: int,
        *,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[WaitlistEntry], int]:
        filters = [WaitlistEntry.client_id == client_id]
        if active_only:
            filters.append(
                WaitlistEntry.status.in_((WaitlistStatus.ACTIVE, WaitlistStatus.MATCHED))
            )
        rows = (
            select(WaitlistEntry)
            .where(*filters)
            .order_by(WaitlistEntry.created_at.desc(), WaitlistEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
        count = select(func.count(WaitlistEntry.id)).where(*filters)
        return list((await self._session.scalars(rows)).all()), int(
            (await self._session.scalar(count)) or 0
        )

    async def list_page(
        self,
        *,
        status: WaitlistStatus | None,
        service_id: int | None,
        limit: int,
        offset: int,
    ) -> tuple[list[WaitlistEntry], int]:
        filters = []
        if status is not None:
            filters.append(WaitlistEntry.status == status)
        if service_id is not None:
            filters.append(WaitlistEntry.service_id == service_id)
        rows = (
            select(WaitlistEntry)
            .where(*filters)
            .order_by(WaitlistEntry.created_at.desc(), WaitlistEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
        count = select(func.count(WaitlistEntry.id)).where(*filters)
        return list((await self._session.scalars(rows)).all()), int(
            (await self._session.scalar(count)) or 0
        )

    async def list_matching(
        self,
        *,
        local_date: date,
        local_time: time,
        window_duration_minutes: int,
        now: datetime,
    ) -> list[WaitlistEntry]:
        result = await self._session.scalars(
            select(WaitlistEntry)
            .join(Service, Service.id == WaitlistEntry.service_id)
            .where(
                WaitlistEntry.status.in_((WaitlistStatus.ACTIVE, WaitlistStatus.MATCHED)),
                WaitlistEntry.date_from <= local_date,
                WaitlistEntry.date_to >= local_date,
                WaitlistEntry.expires_at > now,
                Service.is_active.is_(True),
                Service.duration_max_minutes <= window_duration_minutes,
                or_(
                    func.cardinality(WaitlistEntry.preferred_dates) == 0,
                    WaitlistEntry.preferred_dates.contains([local_date]),
                ),
                or_(
                    WaitlistEntry.preferred_time_from.is_(None),
                    (
                        (WaitlistEntry.preferred_time_from <= local_time)
                        & (WaitlistEntry.preferred_time_to > local_time)
                    ),
                ),
            )
            .order_by(WaitlistEntry.created_at, WaitlistEntry.id)
        )
        return list(result.all())

    async def enqueue_match(
        self,
        *,
        entry_id: int,
        window_id: int,
        scheduled_at: datetime,
    ) -> bool:
        statement = (
            insert(WaitlistNotification)
            .values(
                waitlist_entry_id=entry_id,
                window_id=window_id,
                scheduled_at=scheduled_at,
                available_at=scheduled_at,
            )
            .on_conflict_do_nothing(index_elements=["waitlist_entry_id", "window_id"])
            .returning(WaitlistNotification.id)
        )
        return (await self._session.scalar(statement)) is not None

    async def claim_due_notifications(
        self,
        *,
        now: datetime,
        worker_id: str,
        limit: int,
    ) -> list[WaitlistNotification]:
        result = await self._session.scalars(
            select(WaitlistNotification)
            .where(
                WaitlistNotification.status.in_(
                    (WaitlistNotificationStatus.PENDING, WaitlistNotificationStatus.RETRY)
                ),
                WaitlistNotification.available_at <= now,
            )
            .order_by(WaitlistNotification.available_at, WaitlistNotification.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        jobs = list(result.all())
        for job in jobs:
            job.status = WaitlistNotificationStatus.PROCESSING
            job.locked_at = now
            job.locked_by = worker_id
            job.attempts += 1
        await self._session.flush()
        return jobs
