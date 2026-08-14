"""Owner-authorized physical workstation administration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models import Workstation
from app.domain.errors import EntityNotFoundError, WorkstationStateError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.workstation import (
    WorkstationCreate,
    WorkstationServiceView,
    WorkstationView,
)
from app.services.authorization_service import AuthorizationService

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class WorkstationService:
    """Manage resources and service compatibility without Telegram concerns."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        authorization_service: AuthorizationService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._authorization = authorization_service

    async def list_all(self, actor: StaffContext) -> tuple[WorkstationView, ...]:
        live = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live)
            rows = await unit_of_work.workstations.list_all()
            views: list[WorkstationView] = []
            for row in rows:
                views.append(await self._view(unit_of_work, row))
            return tuple(views)

    async def get(self, actor: StaffContext, workstation_id: int) -> WorkstationView:
        live = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live)
            row = await unit_of_work.workstations.get(workstation_id)
            if row is None:
                raise EntityNotFoundError("Рабочее место больше не существует.")
            return await self._view(unit_of_work, row)

    async def create(
        self,
        actor: StaffContext,
        values: WorkstationCreate,
        *,
        correlation_id: str | None = None,
    ) -> WorkstationView:
        live = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live)
            if await unit_of_work.workstations.get_by_name(values.name) is not None:
                raise WorkstationStateError("Рабочее место с таким названием уже существует.")
            row = await unit_of_work.workstations.add(
                Workstation(
                    business_id=live.business_id,
                    name=values.name,
                    is_active=True,
                )
            )
            await unit_of_work.audit.add(
                actor_user_id=live.user_id,
                action="workstation.created",
                entity_type="workstation",
                entity_id=str(row.id),
                changes={"name": row.name},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, row)

    async def set_service_enabled(
        self,
        actor: StaffContext,
        workstation_id: int,
        service_id: int,
        *,
        enabled: bool,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> WorkstationView:
        live = await self._authorize(actor)
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live)
            row = await unit_of_work.workstations.get(workstation_id, for_update=True)
            service = await unit_of_work.services.get(service_id, for_update=True)
            if row is None:
                raise EntityNotFoundError("Рабочее место больше не существует.")
            if service is None:
                raise EntityNotFoundError("Услуга больше не существует.")
            if enabled and (not row.is_active or not service.is_active):
                raise WorkstationStateError("Сначала активируйте рабочее место и выбранную услугу.")
            if not enabled and await unit_of_work.workstations.has_future_active_windows(
                row.id,
                service_id=service.id,
                now=current,
            ):
                raise WorkstationStateError(
                    "Сначала закройте будущие окна этой услуги на рабочем месте."
                )
            await unit_of_work.workstations.set_service_enabled(
                row.id,
                service.id,
                enabled=enabled,
            )
            await unit_of_work.audit.add(
                actor_user_id=live.user_id,
                action=(
                    "workstation.service_enabled" if enabled else "workstation.service_disabled"
                ),
                entity_type="workstation",
                entity_id=str(row.id),
                changes={"service_id": service.id, "enabled": enabled},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, row)

    async def set_active(
        self,
        actor: StaffContext,
        workstation_id: int,
        *,
        active: bool,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> WorkstationView:
        live = await self._authorize(actor)
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live)
            row = await unit_of_work.workstations.get(workstation_id, for_update=True)
            if row is None:
                raise EntityNotFoundError("Рабочее место больше не существует.")
            if not active and await unit_of_work.workstations.has_future_active_windows(
                row.id,
                now=current,
            ):
                raise WorkstationStateError(
                    "Рабочее место занято будущими окнами. Сначала закройте их."
                )
            row.is_active = active
            row.archived_at = None if active else current
            await unit_of_work.audit.add(
                actor_user_id=live.user_id,
                action="workstation.activated" if active else "workstation.archived",
                entity_type="workstation",
                entity_id=str(row.id),
                changes={"active": active},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, row)

    async def _authorize(self, actor: StaffContext) -> StaffContext:
        return await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.MANAGE_BUSINESS,
        )

    @staticmethod
    async def _view(
        unit_of_work: SqlAlchemyUnitOfWork,
        row: Workstation,
    ) -> WorkstationView:
        service_rows = await unit_of_work.workstations.list_service_rows(row.id)
        return WorkstationView(
            id=row.id,
            name=row.name,
            is_active=row.is_active,
            services=tuple(
                WorkstationServiceView(
                    service_id=service.id,
                    service_name=service.name,
                    service_active=service.is_active,
                    enabled=bool(assignment is not None and assignment.is_active),
                )
                for service, assignment in service_rows
            ),
        )

    @staticmethod
    def _require_tenant(unit_of_work: SqlAlchemyUnitOfWork, actor: StaffContext) -> None:
        if unit_of_work.business_id != actor.business_id:
            raise EntityNotFoundError("Бизнес не найден.")

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
