"""Atomic client and administrator appointment rescheduling."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.database.models import (
    Appointment,
    AppointmentStatusHistory,
    AvailabilityWindow,
    BusinessSettings,
    NotificationJob,
    User,
)
from app.domain.appointments import (
    ensure_active_appointment,
    ensure_client_change_deadline,
)
from app.domain.availability import utc_day_bounds
from app.domain.booking import validate_bookable_date, validate_service_fits_window
from app.domain.enums import AppointmentStatus, AvailabilityWindowStatus
from app.domain.errors import (
    AppointmentNotFoundError,
    AppointmentStateError,
    BookingConflictError,
    BookingLimitError,
)
from app.domain.notifications import future_reminder_schedules
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.appointment import RescheduleAvailability
from app.schemas.booking import BookingReceipt, BookingWindowView, ClientActor
from app.schemas.service import AdminActor
from app.services.appointment_common import appointment_view, ensure_admin, ensure_owner

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]

_WINDOW_TAKEN_MESSAGE = "К сожалению, это время только что заняли. Выберите другое свободное окно."


class RescheduleService:
    """Move an active appointment without exposing a state with neither window."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def list_my_options(
        self,
        actor: ClientActor,
        appointment_id: int,
        *,
        now: datetime | None = None,
    ) -> RescheduleAvailability:
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            client = await self._client(unit_of_work, actor.telegram_id)
            appointment = await self._appointment(unit_of_work, appointment_id)
            ensure_owner(appointment, client)
            settings = await self._settings(unit_of_work)
            old_window = await self._window(unit_of_work, appointment.window_id)
            ensure_active_appointment(appointment.status)
            ensure_client_change_deadline(
                start_at=old_window.start_at,
                now=current_time,
                deadline_hours=settings.cancellation_deadline_hours,
            )
            return await self._options(
                unit_of_work,
                appointment,
                old_window,
                settings,
                current_time,
            )

    async def list_admin_options(
        self,
        actor: AdminActor,
        appointment_id: int,
        *,
        now: datetime | None = None,
    ) -> RescheduleAvailability:
        ensure_admin(actor, self._admin_telegram_ids)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            appointment = await self._appointment(unit_of_work, appointment_id)
            settings = await self._settings(unit_of_work)
            old_window = await self._window(unit_of_work, appointment.window_id)
            ensure_active_appointment(appointment.status)
            return await self._options(
                unit_of_work,
                appointment,
                old_window,
                settings,
                current_time,
            )

    async def reschedule_my(
        self,
        actor: ClientActor,
        appointment_id: int,
        new_window_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> BookingReceipt:
        return await self._reschedule(
            client_actor=actor,
            admin_actor=None,
            appointment_id=appointment_id,
            new_window_id=new_window_id,
            now=now,
            correlation_id=correlation_id,
        )

    async def reschedule_admin(
        self,
        actor: AdminActor,
        appointment_id: int,
        new_window_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> BookingReceipt:
        ensure_admin(actor, self._admin_telegram_ids)
        return await self._reschedule(
            client_actor=None,
            admin_actor=actor,
            appointment_id=appointment_id,
            new_window_id=new_window_id,
            now=now,
            correlation_id=correlation_id,
        )

    async def _reschedule(
        self,
        *,
        client_actor: ClientActor | None,
        admin_actor: AdminActor | None,
        appointment_id: int,
        new_window_id: int,
        now: datetime | None,
        correlation_id: str | None,
    ) -> BookingReceipt:
        current_time = self._aware_now(now)
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                settings = await self._settings(unit_of_work)
                initial_appointment = await self._appointment(unit_of_work, appointment_id)
                client = await unit_of_work.users.get_by_id(initial_appointment.client_id)
                if client is None:
                    raise RuntimeError("Appointment client is missing")
                if client_actor is not None:
                    actor_client = await self._client(unit_of_work, client_actor.telegram_id)
                    ensure_owner(initial_appointment, actor_client)

                old_initial = await self._window(
                    unit_of_work,
                    initial_appointment.window_id,
                )
                new_initial = await self._window(unit_of_work, new_window_id)
                if old_initial.id == new_initial.id:
                    raise AppointmentStateError("Выберите другое окно для переноса.")

                zone = ZoneInfo(settings.timezone)
                local_dates = sorted(
                    {
                        old_initial.start_at.astimezone(zone).date(),
                        new_initial.start_at.astimezone(zone).date(),
                    }
                )
                for local_date in local_dates:
                    await unit_of_work.windows.lock_local_date(local_date)
                locked_windows = await unit_of_work.windows.get_many_for_update(
                    {old_initial.id, new_initial.id}
                )
                locked_by_id = {window.id: window for window in locked_windows}
                if set(locked_by_id) != {old_initial.id, new_initial.id}:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                old_window = locked_by_id[old_initial.id]
                new_window = locked_by_id[new_initial.id]
                appointment = await self._appointment(
                    unit_of_work,
                    appointment_id,
                    for_update=True,
                )
                if appointment.window_id != old_window.id:
                    raise AppointmentStateError("Запись была изменена. Обновите данные.")
                ensure_active_appointment(appointment.status)
                if old_window.status is not AvailabilityWindowStatus.BOOKED:
                    raise AppointmentStateError("Старое окно уже находится в другом состоянии.")
                if client_actor is not None:
                    ensure_owner(appointment, client)
                    ensure_client_change_deadline(
                        start_at=old_window.start_at,
                        now=current_time,
                        deadline_hours=settings.cancellation_deadline_hours,
                    )
                    changed_by_user_id = client.id
                else:
                    if admin_actor is None:
                        raise RuntimeError("Reschedule actor is missing")
                    admin_user = await unit_of_work.users.get_or_create_admin(admin_actor)
                    changed_by_user_id = admin_user.id

                if (
                    new_window.status is not AvailabilityWindowStatus.OPEN
                    or new_window.start_at <= current_time
                ):
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                new_local_date = validate_bookable_date(
                    start_at=new_window.start_at,
                    now=current_time,
                    timezone=settings.timezone,
                    booking_horizon_days=settings.booking_horizon_days,
                    allow_saturday=settings.allow_saturday,
                    allow_sunday=settings.allow_sunday,
                )
                validate_service_fits_window(
                    duration_max_minutes=appointment.duration_max_snapshot,
                    start_at=new_window.start_at,
                    end_at=new_window.end_at,
                )
                day_start, day_end = utc_day_bounds(new_local_date, settings.timezone)
                daily_count = await unit_of_work.appointments.count_capacity_between(
                    day_start,
                    day_end,
                    exclude_appointment_id=appointment.id,
                )
                if daily_count >= settings.max_appointments_per_day:
                    raise BookingLimitError("На эту дату больше нет мест.")

                new_appointment = await unit_of_work.appointments.add(
                    Appointment(
                        client_id=appointment.client_id,
                        window_id=new_window.id,
                        service_id=appointment.service_id,
                        rescheduled_from_id=appointment.id,
                        service_name_snapshot=appointment.service_name_snapshot,
                        price_snapshot=appointment.price_snapshot,
                        duration_min_snapshot=appointment.duration_min_snapshot,
                        duration_max_snapshot=appointment.duration_max_snapshot,
                        status=AppointmentStatus.CONFIRMED,
                        client_comment=appointment.client_comment,
                    )
                )
                previous_status = appointment.status
                appointment.status = AppointmentStatus.RESCHEDULED
                old_window.status = (
                    AvailabilityWindowStatus.OPEN
                    if old_window.start_at > current_time
                    else AvailabilityWindowStatus.CLOSED
                )
                new_window.status = AvailabilityWindowStatus.BOOKED
                await unit_of_work.appointments.add_history(
                    AppointmentStatusHistory(
                        appointment_id=appointment.id,
                        previous_status=previous_status,
                        new_status=AppointmentStatus.RESCHEDULED,
                        changed_by_user_id=changed_by_user_id,
                        reason="Перенос записи",
                    )
                )
                await unit_of_work.appointments.add_history(
                    AppointmentStatusHistory(
                        appointment_id=new_appointment.id,
                        previous_status=None,
                        new_status=AppointmentStatus.CONFIRMED,
                        changed_by_user_id=changed_by_user_id,
                        reason="Создано при переносе",
                    )
                )
                await unit_of_work.notifications.cancel_unsent(appointment.id)
                admin_users = await unit_of_work.users.list_by_telegram_ids(
                    self._admin_telegram_ids
                )
                schedules = future_reminder_schedules(
                    start_at=new_window.start_at,
                    now=current_time,
                    offsets_minutes=settings.reminder_offsets_minutes,
                    client_user_id=client.id,
                    admin_user_ids=[user.id for user in admin_users if not user.is_blocked],
                )
                await unit_of_work.notifications.add_all(
                    [
                        NotificationJob(
                            appointment_id=new_appointment.id,
                            recipient_user_id=schedule.recipient_user_id,
                            notification_type=schedule.notification_type,
                            offset_minutes=schedule.offset_minutes,
                            scheduled_at=schedule.scheduled_at,
                            available_at=schedule.scheduled_at,
                        )
                        for schedule in schedules
                    ]
                )
                await unit_of_work.audit.add(
                    actor_user_id=changed_by_user_id,
                    action="appointment.rescheduled",
                    entity_type="appointment",
                    entity_id=str(appointment.id),
                    changes={
                        "new_appointment_id": new_appointment.id,
                        "old_window_id": old_window.id,
                        "new_window_id": new_window.id,
                    },
                    correlation_id=correlation_id,
                )
                await unit_of_work.commit()
                return BookingReceipt(
                    appointment_id=new_appointment.id,
                    service_name=new_appointment.service_name_snapshot,
                    price=new_appointment.price_snapshot,
                    duration_min_minutes=new_appointment.duration_min_snapshot,
                    duration_max_minutes=new_appointment.duration_max_snapshot,
                    start_at=new_window.start_at,
                    end_at=new_window.end_at,
                    timezone=settings.timezone,
                    address=settings.address,
                    map_url=settings.map_url,
                    master_telegram_url=settings.master_telegram_url,
                    client_name=client.first_name or "—",
                    phone=client.phone or "—",
                )
        except IntegrityError as exc:
            raise BookingConflictError(_WINDOW_TAKEN_MESSAGE) from exc

    async def _options(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        appointment: Appointment,
        old_window: AvailabilityWindow,
        settings: BusinessSettings,
        now: datetime,
    ) -> RescheduleAvailability:
        zone = ZoneInfo(settings.timezone)
        today = now.astimezone(zone).date()
        horizon_last = today + timedelta(days=settings.booking_horizon_days)
        query_start, _ = utc_day_bounds(today, settings.timezone)
        _, query_end = utc_day_bounds(horizon_last, settings.timezone)
        windows = await unit_of_work.windows.list_open_between(max(now, query_start), query_end)
        counts: dict[date, int] = {}
        available: list[BookingWindowView] = []
        for window in windows:
            if window.id == old_window.id:
                continue
            if (
                appointment.duration_max_snapshot * 60
                > (window.end_at - window.start_at).total_seconds()
            ):
                continue
            local_date = window.start_at.astimezone(zone).date()
            if local_date.weekday() == 5 and not settings.allow_saturday:
                continue
            if local_date.weekday() == 6 and not settings.allow_sunday:
                continue
            if local_date not in counts:
                day_start, day_end = utc_day_bounds(local_date, settings.timezone)
                counts[local_date] = await unit_of_work.appointments.count_capacity_between(
                    day_start,
                    day_end,
                    exclude_appointment_id=appointment.id,
                )
            if counts[local_date] >= settings.max_appointments_per_day:
                continue
            available.append(
                BookingWindowView(
                    id=window.id,
                    start_at=window.start_at,
                    end_at=window.end_at,
                    timezone=settings.timezone,
                )
            )
        return RescheduleAvailability(
            appointment=appointment_view(appointment, old_window, settings, now),
            windows=available,
        )

    @staticmethod
    async def _client(unit_of_work: SqlAlchemyUnitOfWork, telegram_id: int) -> User:
        client = await unit_of_work.users.get_by_telegram_id(telegram_id)
        if client is None:
            raise AppointmentNotFoundError("Запись не найдена.")
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
            raise AppointmentNotFoundError("Окно записи не найдено.")
        return window

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
