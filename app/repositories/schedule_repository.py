"""Business- and staff-scoped persistence for lazy schedule projection."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.business import StaffMember
from app.database.models.schedule import StaffScheduleException, StaffWeeklyInterval


class ScheduleRepository:
    """Queries require an explicit business and never return another master's schedule."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_staff_date(
        self, business_id: int, staff_member_id: int, local_date: date
    ) -> None:
        """Serialize schedule edits/projections for one staff member and local date."""

        lock_name = f"schedule:{business_id}:{staff_member_id}:{local_date.isoformat()}"
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(lock_name, 0)))
        )

    async def list_bookable_staff(
        self,
        business_id: int,
        *,
        staff_member_ids: Sequence[int] | None = None,
    ) -> list[StaffMember]:
        statement = select(StaffMember).where(
            StaffMember.business_id == business_id,
            StaffMember.is_active.is_(True),
            StaffMember.is_bookable.is_(True),
            StaffMember.archived_at.is_(None),
        )
        if staff_member_ids is not None:
            if not staff_member_ids:
                return []
            statement = statement.where(StaffMember.id.in_(staff_member_ids))
        rows = await self._session.scalars(
            statement.order_by(StaffMember.sort_order, StaffMember.display_name, StaffMember.id)
        )
        return list(rows.all())

    async def list_weekly_intervals(
        self,
        business_id: int,
        staff_member_ids: Sequence[int],
        weekday: int,
    ) -> list[StaffWeeklyInterval]:
        if not staff_member_ids:
            return []
        rows = await self._session.scalars(
            select(StaffWeeklyInterval)
            .where(
                StaffWeeklyInterval.business_id == business_id,
                StaffWeeklyInterval.staff_member_id.in_(staff_member_ids),
                StaffWeeklyInterval.weekday == weekday,
                StaffWeeklyInterval.is_active.is_(True),
            )
            .order_by(
                StaffWeeklyInterval.staff_member_id,
                StaffWeeklyInterval.start_minute,
                StaffWeeklyInterval.id,
            )
        )
        return list(rows.all())

    async def list_date_exceptions(
        self,
        business_id: int,
        staff_member_ids: Sequence[int],
        local_date: date,
    ) -> list[StaffScheduleException]:
        if not staff_member_ids:
            return []
        rows = await self._session.scalars(
            select(StaffScheduleException)
            .where(
                StaffScheduleException.business_id == business_id,
                StaffScheduleException.staff_member_id.in_(staff_member_ids),
                StaffScheduleException.local_date == local_date,
                StaffScheduleException.archived_at.is_(None),
            )
            .order_by(
                StaffScheduleException.staff_member_id,
                StaffScheduleException.start_minute.nullsfirst(),
                StaffScheduleException.id,
            )
        )
        return list(rows.all())

    async def add_weekly_interval(self, interval: StaffWeeklyInterval) -> StaffWeeklyInterval:
        self._session.add(interval)
        await self._session.flush()
        return interval

    async def add_date_exception(self, exception: StaffScheduleException) -> StaffScheduleException:
        self._session.add(exception)
        await self._session.flush()
        return exception

    async def archive_date_exception(
        self, exception: StaffScheduleException, archived_at: datetime
    ) -> None:
        exception.archived_at = archived_at
        await self._session.flush()
