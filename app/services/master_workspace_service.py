"""Strictly self-scoped master appointment and schedule use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.database.models import (
    Appointment,
    AppointmentStatusHistory,
    AvailabilityWindow,
    BusinessSettings,
    NotificationJob,
    User,
)
from app.database.models.business import StaffMember
from app.domain.appointments import ensure_appointment_transition
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    NotificationType,
    StaffRole,
)
from app.domain.errors import (
    AppointmentNotFoundError,
    AppointmentStateError,
    AuthorizationError,
)
from app.domain.reference_retention import ReferenceRetentionPolicy
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.master_workspace import (
    MasterAppointmentView,
    MasterScheduleExceptionView,
    MasterScheduleView,
    MasterWeeklyIntervalView,
)
from app.services.waitlist_matching import enqueue_waitlist_matches

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class MasterWorkspaceService:
    """Never accepts a master ID from callback data; it uses the live staff context."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        reference_retention_policy: ReferenceRetentionPolicy | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._reference_retention_policy = reference_retention_policy or ReferenceRetentionPolicy()

    async def list_workspace_appointments(
        self,
        actor: StaffContext,
        *,
        now: datetime | None = None,
        history_hours: int = 24,
        days_ahead: int = 30,
        limit: int = 30,
    ) -> tuple[MasterAppointmentView, ...]:
        """List the recent active visits and upcoming queue for the current master only."""

        if not 1 <= history_hours <= 72 or not 1 <= days_ahead <= 90:
            raise ValueError("Workspace appointment range is out of bounds")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            await self._require_live_master(
                unit_of_work,
                actor,
                StaffPermission.VIEW_OWN_APPOINTMENTS,
            )
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings are missing")
            rows = await unit_of_work.appointments.list_between(
                current - timedelta(hours=history_hours),
                current + timedelta(days=days_ahead),
                staff_member_id=actor.staff_member_id,
            )
            return await self._appointment_views(unit_of_work, rows[:limit], settings.timezone)

    async def list_upcoming_appointments(
        self,
        actor: StaffContext,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> tuple[MasterAppointmentView, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            await self._require_live_master(
                unit_of_work,
                actor,
                StaffPermission.VIEW_OWN_APPOINTMENTS,
            )
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings are missing")
            rows = await unit_of_work.appointments.list_upcoming(
                current,
                limit=limit,
                staff_member_id=actor.staff_member_id,
            )
            return await self._appointment_views(unit_of_work, rows, settings.timezone)

    async def complete_own_visit(
        self,
        actor: StaffContext,
        appointment_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> MasterAppointmentView:
        """Complete an ended visit assigned to the authenticated master."""

        return await self._close_own_visit(
            actor,
            appointment_id,
            target=AppointmentStatus.COMPLETED,
            now=now,
            correlation_id=correlation_id,
        )

    async def mark_own_no_show(
        self,
        actor: StaffContext,
        appointment_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> MasterAppointmentView:
        """Mark an ended visit assigned to the authenticated master as a no-show."""

        return await self._close_own_visit(
            actor,
            appointment_id,
            target=AppointmentStatus.NO_SHOW,
            now=now,
            correlation_id=correlation_id,
        )

    async def cancel_own_appointment(
        self,
        actor: StaffContext,
        appointment_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> MasterAppointmentView:
        """Cancel a future confirmed visit assigned to the authenticated master."""

        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            await self._require_live_master(
                unit_of_work,
                actor,
                StaffPermission.MANAGE_OWN_APPOINTMENTS,
            )
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings are missing")
            appointment, window = await self._lock_own_appointment_window(
                unit_of_work,
                actor,
                appointment_id,
                settings.timezone,
            )
            if appointment.status not in {
                AppointmentStatus.CONFIRMED,
                AppointmentStatus.CLIENT_CONFIRMED,
            }:
                raise AppointmentStateError("Отменить можно только активную запись.")
            if window.start_at <= current:
                raise AppointmentStateError("Начавшуюся запись нельзя отменить из панели мастера.")
            if window.status is not AvailabilityWindowStatus.BOOKED:
                raise AppointmentStateError("Окно записи уже изменено.")
            previous = appointment.status
            ensure_appointment_transition(previous, AppointmentStatus.CANCELLED_BY_ADMIN)
            appointment.status = AppointmentStatus.CANCELLED_BY_ADMIN
            appointment.cancelled_at = current
            appointment.cancellation_reason = "Отменено мастером"
            window.status = AvailabilityWindowStatus.OPEN
            await unit_of_work.reference_media.set_expiry_for_appointment(
                appointment.id,
                self._reference_retention_policy.expires_at(
                    status=appointment.status,
                    planned_end_at=window.end_at,
                    cancelled_at=current,
                ),
            )
            await unit_of_work.notifications.cancel_unsent(appointment.id)
            await unit_of_work.appointments.add_history(
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status=previous,
                    new_status=appointment.status,
                    changed_by_user_id=actor.user_id,
                    reason="Отменено мастером",
                )
            )
            await enqueue_waitlist_matches(
                unit_of_work,
                window,
                settings,
                now=current,
                correlation_id=correlation_id,
            )
            await self._audit_appointment_transition(
                unit_of_work,
                actor,
                appointment,
                previous,
                correlation_id=correlation_id,
            )
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            if client is None:
                raise RuntimeError("Appointment client is missing")
            await unit_of_work.commit()
            return self._appointment_view(appointment, window, client, settings.timezone)

    async def _close_own_visit(
        self,
        actor: StaffContext,
        appointment_id: int,
        *,
        target: AppointmentStatus,
        now: datetime | None,
        correlation_id: str | None,
    ) -> MasterAppointmentView:
        if target not in {AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW}:
            raise ValueError("Unsupported master appointment transition")
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            await self._require_live_master(
                unit_of_work,
                actor,
                StaffPermission.MANAGE_OWN_APPOINTMENTS,
            )
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings are missing")
            appointment, window = await self._lock_own_appointment_window(
                unit_of_work,
                actor,
                appointment_id,
                settings.timezone,
            )
            if appointment.status is target:
                client = await unit_of_work.users.get_by_id(appointment.client_id)
                if client is None:
                    raise RuntimeError("Appointment client is missing")
                return self._appointment_view(appointment, window, client, settings.timezone)
            if appointment.status not in {
                AppointmentStatus.CONFIRMED,
                AppointmentStatus.CLIENT_CONFIRMED,
            }:
                raise AppointmentStateError("Изменить можно только активную запись.")
            if target is AppointmentStatus.COMPLETED and window.start_at > current:
                raise AppointmentStateError("Завершить визит можно после начала окна записи.")
            if target is AppointmentStatus.NO_SHOW and window.end_at > current:
                raise AppointmentStateError("Неявку можно отметить после окончания записи.")
            previous = appointment.status
            ensure_appointment_transition(previous, target)
            appointment.status = target
            window.status = AvailabilityWindowStatus.CLOSED
            if target is AppointmentStatus.COMPLETED:
                appointment.completed_at = current
            else:
                appointment.no_show_at = current
            await unit_of_work.reference_media.set_expiry_for_appointment(
                appointment.id,
                self._reference_retention_policy.expires_at(
                    status=target,
                    planned_end_at=window.end_at,
                    completed_at=current if target is AppointmentStatus.COMPLETED else None,
                ),
            )
            await unit_of_work.notifications.cancel_unsent(appointment.id)
            await unit_of_work.appointments.add_history(
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status=previous,
                    new_status=target,
                    changed_by_user_id=actor.user_id,
                    reason="Визит завершён" if target is AppointmentStatus.COMPLETED else "Неявка",
                )
            )
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            if client is None:
                raise RuntimeError("Appointment client is missing")
            if target is AppointmentStatus.COMPLETED:
                await self._enqueue_post_visit_notifications(
                    unit_of_work,
                    appointment,
                    client,
                    settings,
                    current,
                )
            await self._audit_appointment_transition(
                unit_of_work,
                actor,
                appointment,
                previous,
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._appointment_view(appointment, window, client, settings.timezone)

    async def get_schedule(
        self,
        actor: StaffContext,
        *,
        now: datetime | None = None,
        exception_days: int = 30,
    ) -> MasterScheduleView:
        if not 1 <= exception_days <= 90:
            raise ValueError("exception_days must be between 1 and 90")
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            member = await self._require_live_master(
                unit_of_work,
                actor,
                StaffPermission.VIEW_OWN_SCHEDULE,
            )
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings are missing")
            weekly = [
                interval
                for weekday in range(7)
                for interval in await unit_of_work.schedules.list_weekly_intervals(
                    actor.business_id,
                    [actor.staff_member_id],
                    weekday,
                )
            ]
            local_today = current.astimezone(ZoneInfo(settings.timezone)).date()
            exceptions = [
                exception
                for offset in range(exception_days)
                for exception in await unit_of_work.schedules.list_date_exceptions(
                    actor.business_id,
                    [actor.staff_member_id],
                    local_today + timedelta(days=offset),
                )
            ]
            return MasterScheduleView(
                timezone=settings.timezone,
                paused_until=member.schedule_paused_until,
                weekly_intervals=tuple(
                    MasterWeeklyIntervalView(
                        weekday=item.weekday,
                        kind=item.kind,
                        start_minute=item.start_minute,
                        end_minute=item.end_minute,
                    )
                    for item in weekly
                ),
                upcoming_exceptions=tuple(
                    MasterScheduleExceptionView(
                        local_date=item.local_date,
                        kind=item.kind,
                        start_minute=item.start_minute,
                        end_minute=item.end_minute,
                        reason=item.reason,
                    )
                    for item in exceptions
                ),
            )

    async def set_schedule_pause(
        self,
        actor: StaffContext,
        *,
        pause_days: int | None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> MasterScheduleView:
        if pause_days not in {None, 1, 7}:
            raise ValueError("pause_days must be 1, 7 or None")
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            member = await self._require_live_master(
                unit_of_work,
                actor,
                StaffPermission.MANAGE_OWN_SCHEDULE,
                for_update=True,
            )
            previous = member.schedule_paused_until
            member.schedule_paused_until = (
                current + timedelta(days=pause_days) if pause_days is not None else None
            )
            await unit_of_work.staff.flush()
            await unit_of_work.audit.add(
                actor_user_id=actor.user_id,
                action="master.schedule_pause_changed",
                entity_type="staff_member",
                entity_id=str(actor.staff_member_id),
                changes={
                    "before": previous.isoformat() if previous is not None else None,
                    "after": (
                        member.schedule_paused_until.isoformat()
                        if member.schedule_paused_until is not None
                        else None
                    ),
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
        return await self.get_schedule(actor, now=current)

    async def _lock_own_appointment_window(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        actor: StaffContext,
        appointment_id: int,
        timezone: str,
    ) -> tuple[Appointment, AvailabilityWindow]:
        initial = await unit_of_work.appointments.get(appointment_id)
        if initial is None:
            raise AppointmentNotFoundError("Запись не найдена.")
        self._ensure_own_appointment(actor, initial)
        initial_window = await unit_of_work.windows.get(initial.window_id)
        if initial_window is None:
            raise RuntimeError("Appointment window is missing")
        local_date = initial_window.start_at.astimezone(ZoneInfo(timezone)).date()
        await unit_of_work.windows.lock_local_date(
            local_date,
            staff_member_id=actor.staff_member_id,
        )
        locked_windows = await unit_of_work.windows.get_many_for_update({initial.window_id})
        appointment = await unit_of_work.appointments.get(appointment_id, for_update=True)
        if appointment is None:
            raise AppointmentNotFoundError("Запись не найдена.")
        self._ensure_own_appointment(actor, appointment)
        if len(locked_windows) != 1 or appointment.window_id != locked_windows[0].id:
            raise AppointmentStateError("Запись была изменена. Обновите данные.")
        window = locked_windows[0]
        if window.staff_member_id != actor.staff_member_id:
            raise AuthorizationError("Master appointment access denied")
        return appointment, window

    async def _appointment_views(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        rows: list[tuple[Appointment, AvailabilityWindow]],
        timezone: str,
    ) -> tuple[MasterAppointmentView, ...]:
        result: list[MasterAppointmentView] = []
        for appointment, window in rows:
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            if client is None:
                raise RuntimeError("Appointment client is missing")
            result.append(self._appointment_view(appointment, window, client, timezone))
        return tuple(result)

    @staticmethod
    def _appointment_view(
        appointment: Appointment,
        window: AvailabilityWindow,
        client: User,
        timezone: str,
    ) -> MasterAppointmentView:
        return MasterAppointmentView(
            appointment_id=appointment.id,
            service_name=appointment.service_name_snapshot,
            client_name=client.first_name or "Клиент",
            client_phone=client.phone,
            start_at=window.start_at,
            end_at=window.end_at,
            timezone=timezone,
            status=appointment.status,
        )

    @staticmethod
    def _ensure_own_appointment(actor: StaffContext, appointment: Appointment) -> None:
        if (
            appointment.business_id != actor.business_id
            or appointment.staff_member_id != actor.staff_member_id
        ):
            raise AuthorizationError("Master appointment access denied")

    @staticmethod
    async def _enqueue_post_visit_notifications(
        unit_of_work: SqlAlchemyUnitOfWork,
        appointment: Appointment,
        client: User,
        settings: BusinessSettings,
        now: datetime,
    ) -> None:
        jobs: list[NotificationJob] = []
        if settings.reviews_enabled:
            scheduled_at = now + timedelta(minutes=settings.review_request_delay_minutes)
            jobs.append(
                NotificationJob(
                    business_id=unit_of_work.business_id,
                    appointment_id=appointment.id,
                    recipient_user_id=client.id,
                    notification_type=NotificationType.REVIEW_REQUEST,
                    offset_minutes=max(1, settings.review_request_delay_minutes),
                    scheduled_at=scheduled_at,
                    available_at=scheduled_at,
                )
            )
        if (
            client.marketing_consent_at is not None
            and client.repeat_booking_opt_out_at is None
            and not client.is_blocked
        ):
            repeat_at = now + timedelta(days=settings.repeat_booking_reminder_days)
            jobs.append(
                NotificationJob(
                    business_id=unit_of_work.business_id,
                    appointment_id=appointment.id,
                    recipient_user_id=client.id,
                    notification_type=NotificationType.REPEAT_BOOKING_REMINDER,
                    offset_minutes=settings.repeat_booking_reminder_days * 1440,
                    scheduled_at=repeat_at,
                    available_at=repeat_at,
                )
            )
        if jobs:
            await unit_of_work.notifications.add_all(jobs)

    @staticmethod
    async def _audit_appointment_transition(
        unit_of_work: SqlAlchemyUnitOfWork,
        actor: StaffContext,
        appointment: Appointment,
        previous: AppointmentStatus,
        *,
        correlation_id: str | None,
    ) -> None:
        await unit_of_work.audit.add(
            actor_user_id=actor.user_id,
            action=f"master.appointment.{appointment.status.value}",
            entity_type="appointment",
            entity_id=str(appointment.id),
            changes={
                "status": {
                    "before": previous.value,
                    "after": appointment.status.value,
                }
            },
            correlation_id=correlation_id,
        )

    @staticmethod
    async def _require_live_master(
        unit_of_work: SqlAlchemyUnitOfWork,
        actor: StaffContext,
        permission: StaffPermission,
        *,
        for_update: bool = False,
    ) -> StaffMember:
        if actor.role is not StaffRole.MASTER or not actor.has_permission(permission):
            raise AuthorizationError("Master workspace access denied")
        if actor.business_id != unit_of_work.business_id:
            raise AuthorizationError("Master workspace access denied")
        member = await unit_of_work.staff.get_by_id(
            actor.business_id,
            actor.staff_member_id,
            for_update=for_update,
        )
        if (
            member is None
            or member.user_id != actor.user_id
            or member.role is not StaffRole.MASTER
            or not member.is_active
            or member.archived_at is not None
        ):
            raise AuthorizationError("Master workspace access denied")
        return member

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
