"""SQLAlchemy persistence for appointments and their immutable history."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Appointment, AppointmentStatusHistory, AvailabilityWindow
from app.domain.appointments import SCHEDULE_OCCUPYING_STATUSES
from app.domain.enums import AppointmentStatus
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository

FUTURE_ACTIVE_STATUSES = (
    AppointmentStatus.PENDING_PAYMENT,
    AppointmentStatus.PENDING_MANUAL_CONFIRMATION,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.CLIENT_CONFIRMED,
)
CAPACITY_STATUSES = (
    *FUTURE_ACTIVE_STATUSES,
    AppointmentStatus.COMPLETED,
    AppointmentStatus.NO_SHOW,
)


class AppointmentRepository(TenantScopedRepository):
    """Appointment queries sharing the Unit of Work transaction."""

    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def get(
        self,
        appointment_id: int,
        *,
        for_update: bool = False,
    ) -> Appointment | None:
        statement = select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def count_for_future_booking_limit(
        self,
        *,
        client_id: int,
        now: datetime,
        horizon_days: int,
        include_client_cancellations: bool,
    ) -> int:
        """Count rolling quota rows after the caller serialized this client."""

        future = and_(
            Appointment.status.in_(SCHEDULE_OCCUPYING_STATUSES),
            Appointment.scheduled_start_at >= now,
            Appointment.scheduled_start_at < now + timedelta(days=horizon_days),
        )
        conditions = [future]
        if include_client_cancellations:
            conditions.append(
                and_(
                    Appointment.status == AppointmentStatus.CANCELLED_BY_CLIENT,
                    Appointment.cancelled_at.is_not(None),
                    Appointment.cancelled_at >= now - timedelta(days=30),
                    Appointment.cancelled_at <= now,
                )
            )
        return int(
            await self._session.scalar(
                select(func.count(Appointment.id)).where(
                    Appointment.business_id == self.business_id,
                    Appointment.client_id == client_id,
                    or_(*conditions),
                )
            )
            or 0
        )

    async def list_for_client(
        self,
        client_id: int,
        now: datetime,
    ) -> list[tuple[Appointment, AvailabilityWindow]]:
        result = await self._session.execute(
            select(Appointment, AvailabilityWindow)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                Appointment.client_id == client_id,
                Appointment.business_id == self.business_id,
                Appointment.status.in_(FUTURE_ACTIVE_STATUSES),
                AvailabilityWindow.business_id == self.business_id,
                AvailabilityWindow.start_at > now,
            )
            .order_by(AvailabilityWindow.start_at, Appointment.id)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_between(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        staff_member_id: int | None = None,
    ) -> list[tuple[Appointment, AvailabilityWindow]]:
        statement = (
            select(Appointment, AvailabilityWindow)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                Appointment.business_id == self.business_id,
                Appointment.status.in_(FUTURE_ACTIVE_STATUSES),
                AvailabilityWindow.business_id == self.business_id,
                AvailabilityWindow.start_at >= start_at,
                AvailabilityWindow.start_at < end_at,
            )
            .order_by(AvailabilityWindow.start_at, Appointment.id)
        )
        if staff_member_id is not None:
            statement = statement.where(Appointment.staff_member_id == staff_member_id)
        result = await self._session.execute(statement)
        return [(row[0], row[1]) for row in result.all()]

    async def list_upcoming(
        self,
        now: datetime,
        *,
        limit: int = 50,
        staff_member_id: int | None = None,
    ) -> list[tuple[Appointment, AvailabilityWindow]]:
        statement = (
            select(Appointment, AvailabilityWindow)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                Appointment.business_id == self.business_id,
                Appointment.status.in_(FUTURE_ACTIVE_STATUSES),
                AvailabilityWindow.business_id == self.business_id,
                AvailabilityWindow.start_at > now,
            )
            .order_by(AvailabilityWindow.start_at, Appointment.id)
            .limit(limit)
        )
        if staff_member_id is not None:
            statement = statement.where(Appointment.staff_member_id == staff_member_id)
        result = await self._session.execute(statement)
        return [(row[0], row[1]) for row in result.all()]

    async def list_future_active(
        self,
        now: datetime,
    ) -> list[tuple[Appointment, AvailabilityWindow]]:
        result = await self._session.execute(
            select(Appointment, AvailabilityWindow)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                Appointment.business_id == self.business_id,
                Appointment.status.in_(FUTURE_ACTIVE_STATUSES),
                AvailabilityWindow.business_id == self.business_id,
                AvailabilityWindow.start_at > now,
            )
            .order_by(AvailabilityWindow.start_at, Appointment.id)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def count_capacity_between(
        self,
        start_at: datetime,
        end_at: datetime,
        *,
        exclude_appointment_id: int | None = None,
        staff_member_id: int | None = None,
    ) -> int:
        statement = (
            select(func.count(Appointment.id))
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                AvailabilityWindow.start_at >= start_at,
                AvailabilityWindow.start_at < end_at,
                Appointment.business_id == self.business_id,
                AvailabilityWindow.business_id == self.business_id,
                Appointment.status.in_(CAPACITY_STATUSES),
            )
        )
        if staff_member_id is not None:
            statement = statement.where(Appointment.staff_member_id == staff_member_id)
        if exclude_appointment_id is not None:
            statement = statement.where(Appointment.id != exclude_appointment_id)
        return int((await self._session.scalar(statement)) or 0)

    async def add(self, appointment: Appointment) -> Appointment:
        self._require_business(appointment.business_id)
        self._session.add(appointment)
        await self._session.flush()
        return appointment

    async def add_history(self, history: AppointmentStatusHistory) -> None:
        self._session.add(history)
        await self._session.flush()

    async def list_history_for_client(
        self,
        client_id: int,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[Appointment, AvailabilityWindow]], int]:
        filters = [
            Appointment.client_id == client_id,
            Appointment.business_id == self.business_id,
        ]
        rows = await self._session.execute(
            select(Appointment, AvailabilityWindow)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(*filters)
            .order_by(AvailabilityWindow.start_at.desc(), Appointment.id.desc())
            .limit(limit)
            .offset(offset)
        )
        count = int(
            (await self._session.scalar(select(func.count(Appointment.id)).where(*filters))) or 0
        )
        return [(row[0], row[1]) for row in rows.all()], count

    async def count_statuses_for_client(
        self,
        client_id: int,
    ) -> dict[AppointmentStatus, int]:
        rows = await self._session.execute(
            select(Appointment.status, func.count(Appointment.id))
            .where(
                Appointment.client_id == client_id,
                Appointment.business_id == self.business_id,
            )
            .group_by(Appointment.status)
        )
        return {status: int(count) for status, count in rows.all()}

    async def latest_completed_for_client(self, client_id: int) -> Appointment | None:
        return (
            await self._session.scalars(
                select(Appointment)
                .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
                .where(
                    Appointment.client_id == client_id,
                    Appointment.business_id == self.business_id,
                    Appointment.status == AppointmentStatus.COMPLETED,
                )
                .order_by(AvailabilityWindow.start_at.desc(), Appointment.id.desc())
                .limit(1)
            )
        ).one_or_none()

    async def has_future_active_for_client(self, client_id: int, now: datetime) -> bool:
        return bool(
            await self._session.scalar(
                select(func.count(Appointment.id))
                .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
                .where(
                    Appointment.client_id == client_id,
                    Appointment.business_id == self.business_id,
                    Appointment.status.in_(FUTURE_ACTIVE_STATUSES),
                    AvailabilityWindow.business_id == self.business_id,
                    AvailabilityWindow.start_at > now,
                )
            )
        )
