"""SQLAlchemy persistence for appointments and their immutable history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Appointment, AppointmentStatusHistory, AvailabilityWindow
from app.domain.enums import AppointmentStatus

CAPACITY_STATUSES = (
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.CLIENT_CONFIRMED,
    AppointmentStatus.COMPLETED,
    AppointmentStatus.NO_SHOW,
)


class AppointmentRepository:
    """Appointment queries sharing the Unit of Work transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        appointment_id: int,
        *,
        for_update: bool = False,
    ) -> Appointment | None:
        statement = select(Appointment).where(Appointment.id == appointment_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

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
                Appointment.status.in_(CAPACITY_STATUSES[:2]),
                AvailabilityWindow.start_at > now,
            )
            .order_by(AvailabilityWindow.start_at, Appointment.id)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_between(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> list[tuple[Appointment, AvailabilityWindow]]:
        result = await self._session.execute(
            select(Appointment, AvailabilityWindow)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                Appointment.status.in_(CAPACITY_STATUSES[:2]),
                AvailabilityWindow.start_at >= start_at,
                AvailabilityWindow.start_at < end_at,
            )
            .order_by(AvailabilityWindow.start_at, Appointment.id)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_upcoming(
        self,
        now: datetime,
        *,
        limit: int = 50,
    ) -> list[tuple[Appointment, AvailabilityWindow]]:
        result = await self._session.execute(
            select(Appointment, AvailabilityWindow)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                Appointment.status.in_(CAPACITY_STATUSES[:2]),
                AvailabilityWindow.start_at > now,
            )
            .order_by(AvailabilityWindow.start_at, Appointment.id)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_future_active(
        self,
        now: datetime,
    ) -> list[tuple[Appointment, AvailabilityWindow]]:
        result = await self._session.execute(
            select(Appointment, AvailabilityWindow)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                Appointment.status.in_(CAPACITY_STATUSES[:2]),
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
    ) -> int:
        statement = (
            select(func.count(Appointment.id))
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                AvailabilityWindow.start_at >= start_at,
                AvailabilityWindow.start_at < end_at,
                Appointment.status.in_(CAPACITY_STATUSES),
            )
        )
        if exclude_appointment_id is not None:
            statement = statement.where(Appointment.id != exclude_appointment_id)
        return int((await self._session.scalar(statement)) or 0)

    async def add(self, appointment: Appointment) -> Appointment:
        self._session.add(appointment)
        await self._session.flush()
        return appointment

    async def add_history(self, history: AppointmentStatusHistory) -> None:
        self._session.add(history)
        await self._session.flush()
