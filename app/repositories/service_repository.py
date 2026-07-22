"""SQLAlchemy persistence for the editable service catalog."""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Appointment, Service


class ServiceRepository:
    """Data access without catalog business decisions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[Service]:
        result = await self._session.scalars(
            select(Service).where(Service.is_active.is_(True)).order_by(Service.name, Service.id)
        )
        return list(result.all())

    async def list_all(self) -> list[Service]:
        result = await self._session.scalars(
            select(Service).order_by(Service.is_active.desc(), Service.name, Service.id)
        )
        return list(result.all())

    async def get(self, service_id: int, *, for_update: bool = False) -> Service | None:
        statement = select(Service).where(Service.id == service_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def add(self, service: Service) -> Service:
        self._session.add(service)
        await self._session.flush()
        return service

    async def has_appointments(self, service_id: int) -> bool:
        statement = select(exists().where(Appointment.service_id == service_id))
        return bool(await self._session.scalar(statement))

    async def delete(self, service: Service) -> None:
        await self._session.delete(service)
        await self._session.flush()
