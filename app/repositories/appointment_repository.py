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
