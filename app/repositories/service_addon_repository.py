"""Tenant-scoped persistence for service additions and booking snapshots."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.service_addon import AppointmentAddonSnapshot, ServiceAddon
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository


class ServiceAddonRepository(TenantScopedRepository):
    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def list_active(self, service_id: int) -> list[ServiceAddon]:
        rows = await self._session.scalars(
            select(ServiceAddon)
            .where(
                ServiceAddon.business_id == self.business_id,
                ServiceAddon.service_id == service_id,
                ServiceAddon.is_active.is_(True),
            )
            .order_by(ServiceAddon.sort_order, ServiceAddon.name, ServiceAddon.id)
        )
        return list(rows.all())

    async def list_all(self, service_id: int) -> list[ServiceAddon]:
        rows = await self._session.scalars(
            select(ServiceAddon)
            .where(
                ServiceAddon.business_id == self.business_id,
                ServiceAddon.service_id == service_id,
            )
            .order_by(
                ServiceAddon.is_active.desc(),
                ServiceAddon.sort_order,
                ServiceAddon.name,
                ServiceAddon.id,
            )
        )
        return list(rows.all())

    async def get(
        self,
        addon_id: int,
        *,
        service_id: int | None = None,
        for_update: bool = False,
    ) -> ServiceAddon | None:
        statement = select(ServiceAddon).where(
            ServiceAddon.id == addon_id,
            ServiceAddon.business_id == self.business_id,
        )
        if service_id is not None:
            statement = statement.where(ServiceAddon.service_id == service_id)
        if for_update:
            statement = statement.with_for_update()
        rows = await self._session.scalars(statement)
        return rows.one_or_none()

    async def get_selected_for_update(
        self, service_id: int, addon_ids: Sequence[int]
    ) -> list[ServiceAddon]:
        if not addon_ids:
            return []
        rows = await self._session.scalars(
            select(ServiceAddon)
            .where(
                ServiceAddon.business_id == self.business_id,
                ServiceAddon.service_id == service_id,
                ServiceAddon.id.in_(addon_ids),
                ServiceAddon.is_active.is_(True),
            )
            .order_by(ServiceAddon.sort_order, ServiceAddon.id)
            .with_for_update()
        )
        return list(rows.all())

    async def add(self, addon: ServiceAddon) -> ServiceAddon:
        self._require_business(addon.business_id)
        self._session.add(addon)
        await self._session.flush()
        return addon

    async def add_snapshots(
        self, snapshots: Sequence[AppointmentAddonSnapshot]
    ) -> list[AppointmentAddonSnapshot]:
        for snapshot in snapshots:
            self._require_business(snapshot.business_id)
        self._session.add_all(snapshots)
        if snapshots:
            await self._session.flush()
        return list(snapshots)

    async def list_snapshots(self, appointment_id: int) -> list[AppointmentAddonSnapshot]:
        rows = await self._session.scalars(
            select(AppointmentAddonSnapshot)
            .where(
                AppointmentAddonSnapshot.business_id == self.business_id,
                AppointmentAddonSnapshot.appointment_id == appointment_id,
            )
            .order_by(AppointmentAddonSnapshot.position, AppointmentAddonSnapshot.id)
        )
        return list(rows.all())
