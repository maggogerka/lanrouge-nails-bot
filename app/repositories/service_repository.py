"""SQLAlchemy persistence for the editable service catalog."""

from __future__ import annotations

from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Appointment,
    PortfolioItem,
    Service,
    ServiceAddon,
    StaffServiceAssignment,
)
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository


class ServiceRepository(TenantScopedRepository):
    """Data access without catalog business decisions."""

    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def list_active(self) -> list[Service]:
        result = await self._session.scalars(
            select(Service)
            .where(Service.business_id == self.business_id, Service.is_active.is_(True))
            .order_by(Service.name, Service.id)
        )
        return list(result.all())

    async def list_all(self) -> list[Service]:
        result = await self._session.scalars(
            select(Service)
            .where(Service.business_id == self.business_id)
            .order_by(Service.is_active.desc(), Service.name, Service.id)
        )
        return list(result.all())

    async def get(self, service_id: int, *, for_update: bool = False) -> Service | None:
        statement = select(Service).where(
            Service.id == service_id, Service.business_id == self.business_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def add(self, service: Service) -> Service:
        self._require_business(service.business_id)
        self._session.add(service)
        await self._session.flush()
        return service

    async def has_appointments(self, service_id: int) -> bool:
        statement = select(
            exists().where(
                Appointment.service_id == service_id,
                Appointment.business_id == self.business_id,
            )
        )
        return bool(await self._session.scalar(statement))

    async def has_addons(self, service_id: int) -> bool:
        statement = select(
            exists().where(
                ServiceAddon.service_id == service_id,
                ServiceAddon.business_id == self.business_id,
            )
        )
        return bool(await self._session.scalar(statement))

    async def delete(self, service: Service) -> None:
        self._require_business(service.business_id)
        await self._session.execute(
            delete(StaffServiceAssignment).where(
                StaffServiceAssignment.business_id == self.business_id,
                StaffServiceAssignment.service_id == service.id,
            )
        )
        await self._session.execute(
            update(PortfolioItem)
            .where(
                PortfolioItem.business_id == self.business_id,
                PortfolioItem.linked_service_id == service.id,
            )
            .values(linked_service_id=None)
        )
        await self._session.delete(service)
        await self._session.flush()
