"""Appointment queries, cancellation and visit confirmation use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.database.models import (
    Appointment,
    AppointmentStatusHistory,
    AvailabilityWindow,
    BusinessSettings,
    User,
)
from app.domain.appointments import (
    ensure_active_appointment,
    ensure_client_change_deadline,
)
from app.domain.availability import utc_day_bounds
from app.domain.enums import AppointmentStatus, AvailabilityWindowStatus
from app.domain.errors import AppointmentNotFoundError, AppointmentStateError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.appointment import AdminAppointmentView, AppointmentView
from app.schemas.booking import ClientActor
from app.schemas.service import AdminActor
from app.services.appointment_common import (
    admin_appointment_view,
    appointment_view,
    ensure_admin,
    ensure_owner,
)

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class AppointmentService:
    """Keep client ownership and administrator authority inside application code."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def list_my(
        self,
        actor: ClientActor,
        *,
        now: datetime | None = None,
    ) -> list[AppointmentView]:
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            client = await self._client(unit_of_work, actor.telegram_id)
            settings = await self._settings(unit_of_work)
            rows = await unit_of_work.appointments.list_for_client(client.id, current_time)
            return [
                appointment_view(appointment, window, settings, current_time)
                for appointment, window in rows
            ]

    async def get_my(
        self,
        actor: ClientActor,
        appointment_id: int,
        *,
        now: datetime | None = None,
    ) -> AppointmentView:
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            client = await self._client(unit_of_work, actor.telegram_id)
            appointment = await self._appointment(unit_of_work, appointment_id)
            ensure_owner(appointment, client)
            window = await self._window(unit_of_work, appointment.window_id)
            settings = await self._settings(unit_of_work)
            return appointment_view(appointment, window, settings, current_time)

    async def list_admin_today(
        self,
        actor: AdminActor,
        *,
        now: datetime | None = None,
    ) -> list[AdminAppointmentView]:
        ensure_admin(actor, self._admin_telegram_ids)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await self._settings(unit_of_work)
            local_date = current_time.astimezone(ZoneInfo(settings.timezone)).date()
            day_start, day_end = utc_day_bounds(local_date, settings.timezone)
            rows = await unit_of_work.appointments.list_between(day_start, day_end)
            return await self._admin_views(unit_of_work, rows, settings, current_time)

    async def list_admin_upcoming(
        self,
        actor: AdminActor,
        *,
        now: datetime | None = None,
    ) -> list[AdminAppointmentView]:
        ensure_admin(actor, self._admin_telegram_ids)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await self._settings(unit_of_work)
            rows = await unit_of_work.appointments.list_upcoming(current_time)
            return await self._admin_views(unit_of_work, rows, settings, current_time)

    async def get_admin(
        self,
        actor: AdminActor,
        appointment_id: int,
        *,
        now: datetime | None = None,
    ) -> AdminAppointmentView:
        ensure_admin(actor, self._admin_telegram_ids)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            appointment = await self._appointment(unit_of_work, appointment_id)
            window = await self._window(unit_of_work, appointment.window_id)
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            if client is None:
                raise RuntimeError("Appointment client is missing")
            settings = await self._settings(unit_of_work)
            return admin_appointment_view(
                appointment,
                window,
                client,
                settings,
                current_time,
            )

    async def cancel_my(
        self,
        actor: ClientActor,
        appointment_id: int,
        *,
        reason: str | None = None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AppointmentView:
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            client = await self._client(unit_of_work, actor.telegram_id)
            settings = await self._settings(unit_of_work)
            appointment, window = await self._lock_appointment_window(
                unit_of_work,
                appointment_id,
                settings.timezone,
            )
            ensure_owner(appointment, client)
            ensure_active_appointment(appointment.status)
            ensure_client_change_deadline(
                start_at=window.start_at,
                now=current_time,
                deadline_hours=settings.cancellation_deadline_hours,
            )
            await self._cancel(
                unit_of_work,
                appointment,
                window,
                new_status=AppointmentStatus.CANCELLED_BY_CLIENT,
                changed_by_user_id=client.id,
                reason=reason,
                now=current_time,
                reopen=True,
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return appointment_view(appointment, window, settings, current_time)

    async def cancel_admin(
        self,
        actor: AdminActor,
        appointment_id: int,
        *,
        reopen_window: bool,
        reason: str | None = None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AdminAppointmentView:
        ensure_admin(actor, self._admin_telegram_ids)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            settings = await self._settings(unit_of_work)
            appointment, window = await self._lock_appointment_window(
                unit_of_work,
                appointment_id,
                settings.timezone,
            )
            ensure_active_appointment(appointment.status)
            await self._cancel(
                unit_of_work,
                appointment,
                window,
                new_status=AppointmentStatus.CANCELLED_BY_ADMIN,
                changed_by_user_id=actor_user.id,
                reason=reason,
                now=current_time,
                reopen=reopen_window,
                correlation_id=correlation_id,
            )
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            if client is None:
                raise RuntimeError("Appointment client is missing")
            await unit_of_work.commit()
            return admin_appointment_view(
                appointment,
                window,
                client,
                settings,
                current_time,
            )

    async def confirm_visit(
        self,
        actor: AdminActor,
        appointment_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AdminAppointmentView:
        ensure_admin(actor, self._admin_telegram_ids)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            appointment = await self._appointment(unit_of_work, appointment_id, for_update=True)
            if appointment.status is not AppointmentStatus.CONFIRMED:
                raise AppointmentStateError(
                    "Подтвердить визит можно только для новой активной записи."
                )
            previous = appointment.status
            appointment.status = AppointmentStatus.CLIENT_CONFIRMED
            appointment.client_confirmed_at = current_time
            await unit_of_work.appointments.add_history(
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status=previous,
                    new_status=appointment.status,
                    changed_by_user_id=actor_user.id,
                    reason="Подтверждено администратором",
                )
            )
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="appointment.visit_confirmed",
                entity_type="appointment",
                entity_id=str(appointment.id),
                changes={"status": {"before": previous.value, "after": appointment.status.value}},
                correlation_id=correlation_id,
            )
            window = await self._window(unit_of_work, appointment.window_id)
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            settings = await self._settings(unit_of_work)
            if client is None:
                raise RuntimeError("Appointment client is missing")
            await unit_of_work.commit()
            return admin_appointment_view(
                appointment,
                window,
                client,
                settings,
                current_time,
            )

    async def confirm_my_visit(
        self,
        actor: ClientActor,
        appointment_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AppointmentView:
        """Handle the explicit client confirmation from the 24-hour reminder."""

        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            client = await self._client(unit_of_work, actor.telegram_id)
            appointment = await self._appointment(unit_of_work, appointment_id, for_update=True)
            ensure_owner(appointment, client)
            if appointment.status is not AppointmentStatus.CONFIRMED:
                raise AppointmentStateError("Визит уже подтверждён или запись неактивна.")
            appointment.status = AppointmentStatus.CLIENT_CONFIRMED
            appointment.client_confirmed_at = current_time
            await unit_of_work.appointments.add_history(
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status=AppointmentStatus.CONFIRMED,
                    new_status=AppointmentStatus.CLIENT_CONFIRMED,
                    changed_by_user_id=client.id,
                    reason="Подтверждено клиенткой",
                )
            )
            await unit_of_work.audit.add(
                actor_user_id=client.id,
                action="appointment.visit_confirmed",
                entity_type="appointment",
                entity_id=str(appointment.id),
                changes={
                    "status": {
                        "before": AppointmentStatus.CONFIRMED.value,
                        "after": AppointmentStatus.CLIENT_CONFIRMED.value,
                    }
                },
                correlation_id=correlation_id,
            )
            window = await self._window(unit_of_work, appointment.window_id)
            settings = await self._settings(unit_of_work)
            await unit_of_work.commit()
            return appointment_view(appointment, window, settings, current_time)

    async def _lock_appointment_window(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        appointment_id: int,
        timezone: str,
    ) -> tuple[Appointment, AvailabilityWindow]:
        initial = await self._appointment(unit_of_work, appointment_id)
        initial_window = await self._window(unit_of_work, initial.window_id)
        local_date = initial_window.start_at.astimezone(ZoneInfo(timezone)).date()
        await unit_of_work.windows.lock_local_date(local_date)
        locked_windows = await unit_of_work.windows.get_many_for_update({initial.window_id})
        appointment = await self._appointment(unit_of_work, appointment_id, for_update=True)
        if len(locked_windows) != 1 or appointment.window_id != locked_windows[0].id:
            raise AppointmentStateError("Запись была изменена. Обновите данные.")
        return appointment, locked_windows[0]

    async def _cancel(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        appointment: Appointment,
        window: AvailabilityWindow,
        *,
        new_status: AppointmentStatus,
        changed_by_user_id: int,
        reason: str | None,
        now: datetime,
        reopen: bool,
        correlation_id: str | None,
    ) -> None:
        if window.status is not AvailabilityWindowStatus.BOOKED:
            raise AppointmentStateError("Окно записи уже находится в другом состоянии.")
        previous = appointment.status
        appointment.status = new_status
        appointment.cancelled_at = now
        appointment.cancellation_reason = reason
        window.status = (
            AvailabilityWindowStatus.OPEN
            if reopen and window.start_at > now
            else AvailabilityWindowStatus.CLOSED
        )
        await unit_of_work.notifications.cancel_unsent(appointment.id)
        await unit_of_work.appointments.add_history(
            AppointmentStatusHistory(
                appointment_id=appointment.id,
                previous_status=previous,
                new_status=new_status,
                changed_by_user_id=changed_by_user_id,
                reason=reason,
            )
        )
        await unit_of_work.audit.add(
            actor_user_id=changed_by_user_id,
            action="appointment.cancelled",
            entity_type="appointment",
            entity_id=str(appointment.id),
            changes={
                "status": {"before": previous.value, "after": new_status.value},
                "window_status": window.status.value,
                "has_reason": reason is not None,
            },
            correlation_id=correlation_id,
        )

    async def _admin_views(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        rows: list[tuple[Appointment, AvailabilityWindow]],
        settings: BusinessSettings,
        now: datetime,
    ) -> list[AdminAppointmentView]:
        views = []
        for appointment, window in rows:
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            if client is None:
                raise RuntimeError("Appointment client is missing")
            views.append(admin_appointment_view(appointment, window, client, settings, now))
        return views

    @staticmethod
    async def _client(unit_of_work: SqlAlchemyUnitOfWork, telegram_id: int) -> User:
        client = await unit_of_work.users.get_by_telegram_id(telegram_id)
        if client is None:
            raise AppointmentNotFoundError("Записи не найдены.")
        return client

    @staticmethod
    async def _settings(unit_of_work: SqlAlchemyUnitOfWork) -> BusinessSettings:
        settings = await unit_of_work.settings.get()
        if settings is None:
            raise RuntimeError("Business settings row is missing")
        return settings

    @staticmethod
    async def _appointment(
        unit_of_work: SqlAlchemyUnitOfWork,
        appointment_id: int,
        *,
        for_update: bool = False,
    ) -> Appointment:
        appointment = await unit_of_work.appointments.get(
            appointment_id,
            for_update=for_update,
        )
        if appointment is None:
            raise AppointmentNotFoundError("Запись не найдена.")
        return appointment

    @staticmethod
    async def _window(
        unit_of_work: SqlAlchemyUnitOfWork,
        window_id: int,
    ) -> AvailabilityWindow:
        window = await unit_of_work.windows.get(window_id)
        if window is None:
            raise RuntimeError("Appointment window is missing")
        return window

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
