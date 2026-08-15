"""SQLAlchemy persistence and PostgreSQL locks for availability windows."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Appointment, AvailabilityWindow
from app.domain.enums import AvailabilityWindowStatus
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository

_WINDOW_DATE_LOCK_NAMESPACE = 0x4C4E52
_ACTIVE_STATUSES = (
    AvailabilityWindowStatus.OPEN,
    AvailabilityWindowStatus.RESERVED,
    AvailabilityWindowStatus.BOOKED,
)
_ARCHIVED_STATUSES = (
    AvailabilityWindowStatus.CLOSED,
    AvailabilityWindowStatus.EXPIRED,
)


class WindowRepository(TenantScopedRepository):
    """Window queries sharing one Unit of Work transaction."""

    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def lock_local_date(self, local_date: date, *, staff_member_id: int = 1) -> None:
        """Serialize active-window decisions for one business date."""

        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        f"window:{_WINDOW_DATE_LOCK_NAMESPACE}:{self.business_id}:"
                        f"{staff_member_id}:{local_date.isoformat()}",
                        0,
                    )
                )
            )
        )

    async def list_active_between(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        staff_member_id: int = 1,
        exclude_id: int | None = None,
        for_update: bool = False,
    ) -> list[AvailabilityWindow]:
        statement = (
            select(AvailabilityWindow)
            .where(
                AvailabilityWindow.start_at >= start_at,
                AvailabilityWindow.start_at < end_at,
                AvailabilityWindow.business_id == self.business_id,
                AvailabilityWindow.staff_member_id == staff_member_id,
                AvailabilityWindow.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(AvailabilityWindow.start_at, AvailabilityWindow.id)
        )
        if exclude_id is not None:
            statement = statement.where(AvailabilityWindow.id != exclude_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return list(result.all())

    async def list_upcoming(
        self,
        now: datetime,
        *,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[AvailabilityWindow]:
        statement = select(AvailabilityWindow).where(
            AvailabilityWindow.business_id == self.business_id,
            AvailabilityWindow.start_at > now,
        )
        if not include_archived:
            statement = statement.where(AvailabilityWindow.status.not_in(_ARCHIVED_STATUSES))
        result = await self._session.scalars(
            statement.order_by(AvailabilityWindow.start_at, AvailabilityWindow.id).limit(limit)
        )
        return list(result.all())

    async def list_open_between(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        limit: int = 200,
    ) -> list[AvailabilityWindow]:
        result = await self._session.scalars(
            select(AvailabilityWindow)
            .where(
                AvailabilityWindow.status == AvailabilityWindowStatus.OPEN,
                AvailabilityWindow.business_id == self.business_id,
                AvailabilityWindow.start_at >= start_at,
                AvailabilityWindow.start_at < end_at,
            )
            .order_by(AvailabilityWindow.start_at, AvailabilityWindow.id)
            .limit(limit)
        )
        return list(result.all())

    async def get(
        self,
        window_id: int,
        *,
        for_update: bool = False,
    ) -> AvailabilityWindow | None:
        statement = select(AvailabilityWindow).where(
            AvailabilityWindow.id == window_id,
            AvailabilityWindow.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def get_many_for_update(self, window_ids: set[int]) -> list[AvailabilityWindow]:
        """Lock windows in a deterministic order to prevent reschedule deadlocks."""

        result = await self._session.scalars(
            select(AvailabilityWindow)
            .where(
                AvailabilityWindow.id.in_(window_ids),
                AvailabilityWindow.business_id == self.business_id,
            )
            .order_by(AvailabilityWindow.id)
            .with_for_update()
        )
        return list(result.all())

    async def add(self, window: AvailabilityWindow) -> AvailabilityWindow:
        self._require_business(window.business_id)
        self._session.add(window)
        await self._session.flush()
        return window

    async def has_appointments(self, window_id: int) -> bool:
        statement = select(
            exists().where(
                Appointment.window_id == window_id,
                Appointment.business_id == self.business_id,
            )
        )
        return bool(await self._session.scalar(statement))

    async def delete(self, window: AvailabilityWindow) -> None:
        self._require_business(window.business_id)
        await self._session.delete(window)
        await self._session.flush()
