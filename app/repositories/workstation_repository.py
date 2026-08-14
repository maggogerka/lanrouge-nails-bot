"""Tenant-scoped persistence and concurrency control for workstations."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AvailabilityWindow,
    Service,
    Workstation,
    WorkstationService,
)
from app.domain.enums import AvailabilityWindowStatus
from app.repositories.scoped import TenantScopedRepository

_ACTIVE_WINDOW_STATUSES = (
    AvailabilityWindowStatus.OPEN,
    AvailabilityWindowStatus.RESERVED,
    AvailabilityWindowStatus.BOOKED,
)
_RESOURCE_LOCK_NAMESPACE = 0x575354


class WorkstationRepository(TenantScopedRepository):
    """Keep workstation allocation atomic across concurrent window creation."""

    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def list_all(self) -> list[Workstation]:
        rows = await self._session.scalars(
            select(Workstation)
            .where(Workstation.business_id == self.business_id)
            .order_by(
                Workstation.is_active.desc(),
                Workstation.sort_order,
                func.lower(Workstation.name),
                Workstation.id,
            )
        )
        return list(rows.all())

    async def get(self, workstation_id: int, *, for_update: bool = False) -> Workstation | None:
        statement = select(Workstation).where(
            Workstation.id == workstation_id,
            Workstation.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_name(self, name: str) -> Workstation | None:
        return (
            await self._session.scalars(
                select(Workstation).where(
                    Workstation.business_id == self.business_id,
                    Workstation.archived_at.is_(None),
                    func.lower(Workstation.name) == name.casefold(),
                )
            )
        ).one_or_none()

    async def add(self, workstation: Workstation) -> Workstation:
        self._require_business(workstation.business_id)
        self._session.add(workstation)
        await self._session.flush()
        return workstation

    async def list_service_rows(
        self, workstation_id: int
    ) -> list[tuple[Service, WorkstationService | None]]:
        rows = await self._session.execute(
            select(Service, WorkstationService)
            .outerjoin(
                WorkstationService,
                (WorkstationService.service_id == Service.id)
                & (WorkstationService.workstation_id == workstation_id)
                & (WorkstationService.business_id == self.business_id),
            )
            .where(Service.business_id == self.business_id)
            .order_by(Service.is_active.desc(), Service.sort_order, Service.name, Service.id)
        )
        return [(row[0], row[1]) for row in rows.all()]

    async def set_service_enabled(
        self,
        workstation_id: int,
        service_id: int,
        *,
        enabled: bool,
    ) -> WorkstationService:
        statement = select(WorkstationService).where(
            WorkstationService.business_id == self.business_id,
            WorkstationService.workstation_id == workstation_id,
            WorkstationService.service_id == service_id,
        )
        row = (await self._session.scalars(statement.with_for_update())).one_or_none()
        if row is None:
            row = WorkstationService(
                business_id=self.business_id,
                workstation_id=workstation_id,
                service_id=service_id,
                is_active=enabled,
            )
            self._session.add(row)
        else:
            row.is_active = enabled
        await self._session.flush()
        return row

    async def list_active_for_service(self, service_id: int) -> list[Workstation]:
        rows = await self._session.scalars(
            select(Workstation)
            .join(
                WorkstationService,
                WorkstationService.workstation_id == Workstation.id,
            )
            .where(
                Workstation.business_id == self.business_id,
                Workstation.is_active.is_(True),
                Workstation.archived_at.is_(None),
                WorkstationService.business_id == self.business_id,
                WorkstationService.service_id == service_id,
                WorkstationService.is_active.is_(True),
            )
            .order_by(Workstation.sort_order, Workstation.id)
        )
        return list(rows.all())

    async def lock_service_date(self, service_id: int, local_date: date) -> None:
        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(
                        f"workstation:{_RESOURCE_LOCK_NAMESPACE}:{self.business_id}:"
                        f"{service_id}:{local_date.isoformat()}",
                        0,
                    )
                )
            )
        )

    async def allocate_available(
        self,
        service_id: int,
        *,
        start_at: datetime,
        end_at: datetime,
        exclude_window_id: int | None = None,
    ) -> Workstation | None:
        overlap = (
            select(AvailabilityWindow.id)
            .where(
                AvailabilityWindow.business_id == self.business_id,
                AvailabilityWindow.workstation_id == Workstation.id,
                AvailabilityWindow.status.in_(_ACTIVE_WINDOW_STATUSES),
                AvailabilityWindow.start_at < end_at,
                AvailabilityWindow.end_at > start_at,
            )
            .correlate(Workstation)
        )
        if exclude_window_id is not None:
            overlap = overlap.where(AvailabilityWindow.id != exclude_window_id)
        statement = (
            select(Workstation)
            .join(
                WorkstationService,
                WorkstationService.workstation_id == Workstation.id,
            )
            .where(
                Workstation.business_id == self.business_id,
                Workstation.is_active.is_(True),
                Workstation.archived_at.is_(None),
                WorkstationService.business_id == self.business_id,
                WorkstationService.service_id == service_id,
                WorkstationService.is_active.is_(True),
                ~exists(overlap),
            )
            .order_by(Workstation.sort_order, Workstation.id)
            .limit(1)
            .with_for_update()
        )
        return (await self._session.scalars(statement)).one_or_none()

    async def is_available(
        self,
        workstation_id: int,
        service_id: int,
        *,
        start_at: datetime,
        end_at: datetime,
        exclude_window_id: int | None = None,
    ) -> bool:
        overlap = select(AvailabilityWindow.id).where(
            AvailabilityWindow.business_id == self.business_id,
            AvailabilityWindow.workstation_id == workstation_id,
            AvailabilityWindow.status.in_(_ACTIVE_WINDOW_STATUSES),
            AvailabilityWindow.start_at < end_at,
            AvailabilityWindow.end_at > start_at,
        )
        if exclude_window_id is not None:
            overlap = overlap.where(AvailabilityWindow.id != exclude_window_id)
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        Workstation.id == workstation_id,
                        Workstation.business_id == self.business_id,
                        Workstation.is_active.is_(True),
                        Workstation.archived_at.is_(None),
                        WorkstationService.workstation_id == workstation_id,
                        WorkstationService.service_id == service_id,
                        WorkstationService.business_id == self.business_id,
                        WorkstationService.is_active.is_(True),
                        ~exists(overlap),
                    )
                )
            )
        )

    async def has_future_active_windows(
        self,
        workstation_id: int,
        *,
        now: datetime,
        service_id: int | None = None,
    ) -> bool:
        conditions = [
            AvailabilityWindow.business_id == self.business_id,
            AvailabilityWindow.workstation_id == workstation_id,
            AvailabilityWindow.status.in_(_ACTIVE_WINDOW_STATUSES),
            AvailabilityWindow.end_at > now,
        ]
        if service_id is not None:
            conditions.append(AvailabilityWindow.service_id == service_id)
        return bool(await self._session.scalar(select(exists().where(*conditions))))
