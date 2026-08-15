"""Administrative availability-window use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.database.models import AvailabilityWindow, BusinessSettings, StaffMember, User
from app.domain.availability import (
    ExistingInterval,
    WindowRules,
    local_window_to_utc,
    utc_day_bounds,
    validate_calendar_rules,
    validate_capacity_and_spacing,
)
from app.domain.enums import AvailabilityWindowStatus
from app.domain.errors import (
    AuthorizationError,
    EntityNotFoundError,
    WindowInUseError,
    WindowStateError,
    WindowValidationError,
)
from app.domain.tenancy import DEFAULT_STAFF_MEMBER_ID
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext
from app.schemas.availability import (
    AvailabilityWindowCreate,
    AvailabilityWindowList,
    AvailabilityWindowPreview,
    AvailabilityWindowView,
)
from app.schemas.service import AdminActor, ServiceView
from app.schemas.settings import BusinessSettingsView
from app.services.appointment_common import ensure_admin, ensure_owner_admin
from app.services.waitlist_matching import enqueue_waitlist_matches

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class AvailabilityService:
    """Coordinate calendar validation, PostgreSQL locks and audit writes."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def list_windows(
        self,
        actor: AdminActor,
        *,
        include_archived: bool = False,
        now: datetime | None = None,
    ) -> AvailabilityWindowList:
        self._ensure_admin(actor)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await self._settings(unit_of_work)
            windows = await unit_of_work.windows.list_upcoming(
                current_time,
                include_archived=include_archived,
            )
            return AvailabilityWindowList(
                timezone=settings.timezone,
                windows=[
                    await self._view(unit_of_work, window, settings.timezone) for window in windows
                ],
            )

    async def list_services_for_staff(
        self,
        actor: AdminActor,
        staff_member_id: int,
    ) -> list[ServiceView]:
        """Return services a selected master can actually accept."""

        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            rows = await unit_of_work.service_assignments.list_bookable_services_for_staff(
                unit_of_work.business_id,
                staff_member_id,
            )
            return [ServiceView.model_validate(service) for _, service in rows]

    async def get_window(
        self,
        actor: AdminActor,
        window_id: int,
    ) -> AvailabilityWindowView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await self._settings(unit_of_work)
            window = await self._window(unit_of_work, window_id)
            return await self._view(unit_of_work, window, settings.timezone)

    async def create_window(
        self,
        actor: AdminActor | StaffContext,
        values: AvailabilityWindowCreate,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AvailabilityWindowView:
        staff_member_id = values.staff_member_id or DEFAULT_STAFF_MEMBER_ID
        self._ensure_window_actor(actor, staff_member_id)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            if isinstance(actor, StaffContext) and actor.business_id != unit_of_work.business_id:
                raise AuthorizationError("Окна другого бизнеса недоступны.")
            actor_user = await self._actor_user(unit_of_work, actor)
            settings = await self._settings(unit_of_work)
            rules = self._rules(settings)
            duration = values.duration_minutes or settings.default_window_duration_minutes
            start_at, end_at = local_window_to_utc(
                values.local_date,
                values.local_start_time,
                duration,
                settings.timezone,
            )
            validate_calendar_rules(
                local_date=values.local_date,
                start_at=start_at,
                end_at=end_at,
                now=current_time,
                rules=rules,
            )

            master = await self._lock_and_validate_active_interval(
                unit_of_work,
                settings,
                local_date=values.local_date,
                start_at=start_at,
                end_at=end_at,
                staff_member_id=staff_member_id,
                validate_schedule=values.status is AvailabilityWindowStatus.OPEN,
            )

            window = await unit_of_work.windows.add(
                AvailabilityWindow(
                    business_id=unit_of_work.business_id,
                    staff_member_id=master.id,
                    service_id=None,
                    workstation_id=None,
                    start_at=start_at,
                    end_at=end_at,
                    status=values.status,
                    admin_comment=values.admin_comment,
                    created_by=actor_user.id,
                )
            )
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="availability_window.created",
                entity_type="availability_window",
                entity_id=str(window.id),
                changes={"after": self._audit_values(window)},
                correlation_id=correlation_id,
            )
            if window.status is AvailabilityWindowStatus.OPEN:
                await enqueue_waitlist_matches(
                    unit_of_work,
                    window,
                    settings,
                    now=current_time,
                    correlation_id=correlation_id,
                )
            await unit_of_work.commit()
            return await self._view(unit_of_work, window, settings.timezone)

    async def preview_window(
        self,
        actor: AdminActor | StaffContext,
        values: AvailabilityWindowCreate,
        *,
        now: datetime | None = None,
    ) -> AvailabilityWindowPreview:
        """Validate a draft without persisting it; final creation validates again."""

        staff_member_id = values.staff_member_id or DEFAULT_STAFF_MEMBER_ID
        self._ensure_window_actor(actor, staff_member_id)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            if isinstance(actor, StaffContext) and actor.business_id != unit_of_work.business_id:
                raise AuthorizationError("Окна другого бизнеса недоступны.")
            settings = await self._settings(unit_of_work)
            duration = values.duration_minutes or settings.default_window_duration_minutes
            start_at, end_at = local_window_to_utc(
                values.local_date,
                values.local_start_time,
                duration,
                settings.timezone,
            )
            validate_calendar_rules(
                local_date=values.local_date,
                start_at=start_at,
                end_at=end_at,
                now=current_time,
                rules=self._rules(settings),
            )
            master = await self._lock_and_validate_active_interval(
                unit_of_work,
                settings,
                local_date=values.local_date,
                start_at=start_at,
                end_at=end_at,
                staff_member_id=staff_member_id,
                validate_schedule=values.status is AvailabilityWindowStatus.OPEN,
            )
            return AvailabilityWindowPreview(
                start_at=start_at,
                end_at=end_at,
                staff_member_id=master.id,
                master_name=master.display_name,
                duration_minutes=duration,
                admin_comment=values.admin_comment,
                timezone=settings.timezone,
            )

    async def get_creation_settings(self, actor: StaffContext) -> BusinessSettingsView:
        """Return safe calendar rules for a master opening only their own time."""

        self._ensure_window_actor(actor, actor.staff_member_id)
        async with self._unit_of_work_factory() as unit_of_work:
            if actor.business_id != unit_of_work.business_id:
                raise AuthorizationError("Настройки другого бизнеса недоступны.")
            settings = await self._settings(unit_of_work)
            return BusinessSettingsView.model_validate(settings)

    async def close_window(
        self,
        actor: AdminActor,
        window_id: int,
        *,
        correlation_id: str | None = None,
    ) -> AvailabilityWindowView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            settings = await self._settings(unit_of_work)
            window = await self._window(unit_of_work, window_id, for_update=True)
            if window.status is not AvailabilityWindowStatus.OPEN:
                raise WindowStateError("Закрыть можно только свободное открытое окно.")
            window.status = AvailabilityWindowStatus.CLOSED
            await unit_of_work.session.flush()
            await self._audit_status(
                unit_of_work,
                actor_user.id,
                window,
                before=AvailabilityWindowStatus.OPEN,
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, window, settings.timezone)

    async def reopen_window(
        self,
        actor: AdminActor,
        window_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AvailabilityWindowView:
        self._ensure_admin(actor)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            settings = await self._settings(unit_of_work)
            window = await self._window(unit_of_work, window_id, for_update=True)
            if window.status is not AvailabilityWindowStatus.CLOSED:
                raise WindowStateError("Открыть повторно можно только закрытое окно.")
            local_date = window.start_at.astimezone(ZoneInfo(settings.timezone)).date()
            validate_calendar_rules(
                local_date=local_date,
                start_at=window.start_at,
                end_at=window.end_at,
                now=current_time,
                rules=self._rules(settings),
            )
            await self._lock_and_validate_active_interval(
                unit_of_work,
                settings,
                local_date=local_date,
                start_at=window.start_at,
                end_at=window.end_at,
                staff_member_id=window.staff_member_id,
                exclude_id=window.id,
            )
            window.status = AvailabilityWindowStatus.OPEN
            await unit_of_work.session.flush()
            await self._audit_status(
                unit_of_work,
                actor_user.id,
                window,
                before=AvailabilityWindowStatus.CLOSED,
                correlation_id=correlation_id,
            )
            await enqueue_waitlist_matches(
                unit_of_work,
                window,
                settings,
                now=current_time,
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, window, settings.timezone)

    async def delete_unused_window(
        self,
        actor: AdminActor,
        window_id: int,
        *,
        correlation_id: str | None = None,
    ) -> None:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            window = await self._window(unit_of_work, window_id, for_update=True)
            if window.status in {
                AvailabilityWindowStatus.RESERVED,
                AvailabilityWindowStatus.BOOKED,
            }:
                raise WindowInUseError("Занятое или зарезервированное окно удалить нельзя.")
            if await unit_of_work.windows.has_appointments(window_id):
                raise WindowInUseError("У окна есть история записей; используйте закрытие.")
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="availability_window.deleted",
                entity_type="availability_window",
                entity_id=str(window.id),
                changes={"before": self._audit_values(window)},
                correlation_id=correlation_id,
            )
            await unit_of_work.windows.delete(window)
            await unit_of_work.commit()

    async def force_delete_window(
        self,
        actor: AdminActor,
        window_id: int,
        *,
        correlation_id: str | None = None,
    ) -> int:
        """Permanently remove a window and its appointment aggregate."""

        ensure_owner_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            ensure_owner_admin(
                actor,
                self._admin_telegram_ids,
                business_id=unit_of_work.business_id,
            )
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            window = await self._window(unit_of_work, window_id, for_update=True)
            before = self._audit_values(window)
            deleted_appointments = await unit_of_work.hard_delete.delete_window_with_history(
                window_id
            )
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="availability_window.force_deleted",
                entity_type="availability_window",
                entity_id=str(window_id),
                changes={
                    "before": before,
                    "deleted_appointments": deleted_appointments,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return deleted_appointments

    async def _lock_and_validate_active_interval(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        settings: BusinessSettings,
        *,
        local_date: date,
        start_at: datetime,
        end_at: datetime,
        staff_member_id: int = DEFAULT_STAFF_MEMBER_ID,
        exclude_id: int | None = None,
        validate_schedule: bool = True,
    ) -> StaffMember:
        master = await unit_of_work.staff.get_by_id(
            unit_of_work.business_id,
            staff_member_id,
            for_update=True,
        )
        if master is None or not master.is_active or not master.is_bookable:
            raise WindowValidationError(
                "Мастер недоступен для записи. Проверьте его профиль в разделе сотрудников."
            )
        assignments = await unit_of_work.service_assignments.list_bookable_services_for_staff(
            unit_of_work.business_id,
            staff_member_id,
        )
        if not assignments:
            raise WindowValidationError(
                "У мастера нет услуг для онлайн-записи. Сначала назначьте их в его карточке."
            )
        duration_minutes = int((end_at - start_at).total_seconds() // 60)
        fitting_services = [
            service
            for _, service in assignments
            if service.duration_max_minutes <= duration_minutes
        ]
        if not fitting_services:
            shortest = min(service.duration_max_minutes for _, service in assignments)
            raise WindowValidationError(
                f"Окно слишком короткое: самая короткая доступная услуга требует до {shortest} мин."
            )
        has_resource = False
        for service in fitting_services:
            if await unit_of_work.workstations.list_active_for_service(service.id):
                has_resource = True
                break
        if not has_resource:
            raise WindowValidationError(
                "Для услуг мастера не настроено ни одного рабочего места. "
                "Откройте «Настройки бизнеса → Рабочие места»."
            )
        if not validate_schedule:
            return master
        await unit_of_work.windows.lock_local_date(local_date, staff_member_id=staff_member_id)
        day_start, day_end = utc_day_bounds(local_date, settings.timezone)
        existing = await unit_of_work.windows.list_active_between(
            day_start,
            day_end,
            staff_member_id=staff_member_id,
            exclude_id=exclude_id,
            for_update=True,
        )
        validate_capacity_and_spacing(
            start_at=start_at,
            end_at=end_at,
            existing=[ExistingInterval(window.start_at, window.end_at) for window in existing],
            max_windows_per_day=settings.max_appointments_per_day,
            minimum_gap_minutes=settings.minimum_gap_minutes,
        )
        return master

    async def _audit_status(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        actor_user_id: int,
        window: AvailabilityWindow,
        *,
        before: AvailabilityWindowStatus,
        correlation_id: str | None,
    ) -> None:
        await unit_of_work.audit.add(
            actor_user_id=actor_user_id,
            action="availability_window.status_changed",
            entity_type="availability_window",
            entity_id=str(window.id),
            changes={"status": {"before": before.value, "after": window.status.value}},
            correlation_id=correlation_id,
        )

    def _ensure_admin(self, actor: AdminActor) -> None:
        ensure_admin(actor, self._admin_telegram_ids)

    def _ensure_window_actor(
        self,
        actor: AdminActor | StaffContext,
        staff_member_id: int,
    ) -> None:
        if isinstance(actor, StaffContext):
            if actor.staff_member_id != staff_member_id or not actor.is_bookable:
                raise AuthorizationError("Мастер может открывать окна только для себя.")
            return
        self._ensure_admin(actor)

    @staticmethod
    async def _actor_user(
        unit_of_work: SqlAlchemyUnitOfWork,
        actor: AdminActor | StaffContext,
    ) -> User:
        if isinstance(actor, StaffContext):
            user = await unit_of_work.users.get_by_id(actor.user_id)
            if user is None:
                raise AuthorizationError("Профиль мастера больше недоступен.")
            return user
        return await unit_of_work.users.get_or_create_admin(actor)

    @staticmethod
    async def _settings(unit_of_work: SqlAlchemyUnitOfWork) -> BusinessSettings:
        settings = await unit_of_work.settings.get()
        if settings is None:
            raise RuntimeError("Business settings row is missing")
        return settings

    @staticmethod
    async def _window(
        unit_of_work: SqlAlchemyUnitOfWork,
        window_id: int,
        *,
        for_update: bool = False,
    ) -> AvailabilityWindow:
        window = await unit_of_work.windows.get(window_id, for_update=for_update)
        if window is None:
            raise EntityNotFoundError("Окно не найдено.")
        return window

    @staticmethod
    def _rules(settings: BusinessSettings) -> WindowRules:
        return WindowRules(
            timezone=settings.timezone,
            booking_horizon_days=settings.booking_horizon_days,
            max_windows_per_day=settings.max_appointments_per_day,
            default_duration_minutes=settings.default_window_duration_minutes,
            minimum_gap_minutes=settings.minimum_gap_minutes,
            allow_saturday=settings.allow_saturday,
            allow_sunday=settings.allow_sunday,
        )

    @staticmethod
    async def _view(
        unit_of_work: SqlAlchemyUnitOfWork,
        window: AvailabilityWindow,
        timezone: str,
    ) -> AvailabilityWindowView:
        master = await unit_of_work.staff.get_by_id(
            unit_of_work.business_id,
            window.staff_member_id,
        )
        service = (
            await unit_of_work.services.get(window.service_id)
            if window.service_id is not None
            else None
        )
        workstation = (
            await unit_of_work.workstations.get(window.workstation_id)
            if window.workstation_id is not None
            else None
        )
        return AvailabilityWindowView(
            id=window.id,
            staff_member_id=window.staff_member_id,
            master_name=master.display_name if master is not None else None,
            service_id=window.service_id,
            service_name=service.name if service is not None else None,
            workstation_id=window.workstation_id,
            workstation_name=workstation.name if workstation is not None else None,
            start_at=window.start_at,
            end_at=window.end_at,
            status=window.status,
            admin_comment=window.admin_comment,
            timezone=timezone,
        )

    @staticmethod
    def _audit_values(window: AvailabilityWindow) -> dict[str, object]:
        return {
            "start_at": window.start_at.isoformat(),
            "end_at": window.end_at.isoformat(),
            "status": window.status.value,
            "service_id": window.service_id,
            "workstation_id": window.workstation_id,
            "has_admin_comment": window.admin_comment is not None,
        }

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
