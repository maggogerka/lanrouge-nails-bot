"""Client availability queries and transactional appointment creation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.database.models import (
    Appointment,
    AppointmentStatusHistory,
    BusinessSettings,
    NotificationJob,
    Service,
    User,
)
from app.domain.availability import utc_day_bounds
from app.domain.booking import validate_service_fits_window
from app.domain.enums import AppointmentStatus, AvailabilityWindowStatus
from app.domain.errors import (
    BookingConflictError,
    BookingLimitError,
    BookingUnavailableError,
    PrivacyConsentRequiredError,
)
from app.domain.notifications import future_reminder_schedules
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import (
    BookingAvailability,
    BookingReceipt,
    BookingRequest,
    BookingWindowView,
    BusinessInfo,
    ClientActor,
)
from app.schemas.service import ServiceView

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]

_WINDOW_TAKEN_MESSAGE = "К сожалению, это время только что заняли. Выберите другое свободное окно."


class BookingService:
    """Expose safe availability and atomically occupy exactly one window."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
        *,
        privacy_policy_configured: bool = True,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids
        self._privacy_policy_configured = privacy_policy_configured

    async def list_active_services(self, actor: ClientActor) -> list[ServiceView]:
        self._ensure_privacy_policy()
        async with self._unit_of_work_factory() as unit_of_work:
            await self._consented_client(unit_of_work, actor.telegram_id)
            services = await unit_of_work.services.list_active()
            return [ServiceView.model_validate(service) for service in services]

    async def get_business_info(self, actor: ClientActor) -> BusinessInfo:
        self._ensure_privacy_policy()
        async with self._unit_of_work_factory() as unit_of_work:
            await self._consented_client(unit_of_work, actor.telegram_id)
            settings = await self._settings(unit_of_work)
            return BusinessInfo(
                business_name=settings.business_name,
                address=settings.address,
                map_url=settings.map_url,
                master_telegram_url=settings.master_telegram_url,
            )

    async def list_availability(
        self,
        actor: ClientActor,
        service_id: int,
        *,
        local_date: date | None = None,
        now: datetime | None = None,
    ) -> BookingAvailability:
        self._ensure_privacy_policy()
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            await self._consented_client(unit_of_work, actor.telegram_id)
            settings = await self._settings(unit_of_work)
            service = self._active_service(await unit_of_work.services.get(service_id))

            zone = ZoneInfo(settings.timezone)
            today = current_time.astimezone(zone).date()
            horizon_end = today + timedelta(days=settings.booking_horizon_days + 1)
            query_start, _ = utc_day_bounds(today, settings.timezone)
            _, query_end = utc_day_bounds(horizon_end - timedelta(days=1), settings.timezone)
            windows = await unit_of_work.windows.list_open_between(
                max(current_time, query_start),
                query_end,
            )

            candidates = []
            for window in windows:
                candidate_date = window.start_at.astimezone(zone).date()
                if local_date is not None and candidate_date != local_date:
                    continue
                if self._service_fits(service, window.start_at, window.end_at):
                    candidates.append(window)

            available = []
            counts: dict[date, int] = {}
            for window in candidates:
                candidate_date = window.start_at.astimezone(zone).date()
                if candidate_date not in counts:
                    day_start, day_end = utc_day_bounds(candidate_date, settings.timezone)
                    counts[candidate_date] = await unit_of_work.appointments.count_capacity_between(
                        day_start,
                        day_end,
                    )
                if counts[candidate_date] < settings.max_appointments_per_day:
                    available.append(
                        BookingWindowView(
                            id=window.id,
                            start_at=window.start_at,
                            end_at=window.end_at,
                            timezone=settings.timezone,
                        )
                    )

            return BookingAvailability(
                service=ServiceView.model_validate(service),
                timezone=settings.timezone,
                windows=available,
            )

    async def book(
        self,
        actor: ClientActor,
        values: BookingRequest,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> BookingReceipt:
        self._ensure_privacy_policy()
        current_time = self._aware_now(now)
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                settings = await self._settings(unit_of_work)
                initial_window = await unit_of_work.windows.get(values.window_id)
                if initial_window is None:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                local_date = initial_window.start_at.astimezone(ZoneInfo(settings.timezone)).date()
                await unit_of_work.windows.lock_local_date(local_date)

                window = await unit_of_work.windows.get(values.window_id, for_update=True)
                if window is None or window.status is not AvailabilityWindowStatus.OPEN:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                client = await self._consented_client(
                    unit_of_work,
                    actor.telegram_id,
                    for_update=True,
                )
                service = self._active_service(
                    await unit_of_work.services.get(values.service_id, for_update=True)
                )

                if window.start_at <= current_time:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                validate_service_fits_window(
                    duration_max_minutes=service.duration_max_minutes,
                    start_at=window.start_at,
                    end_at=window.end_at,
                )
                actual_local_date = window.start_at.astimezone(ZoneInfo(settings.timezone)).date()
                if actual_local_date != local_date:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                day_start, day_end = utc_day_bounds(local_date, settings.timezone)
                daily_count = await unit_of_work.appointments.count_capacity_between(
                    day_start,
                    day_end,
                )
                if daily_count >= settings.max_appointments_per_day:
                    raise BookingLimitError("На эту дату больше нет мест.")

                await unit_of_work.users.update_booking_profile(
                    client,
                    name=values.client_name,
                    phone=values.phone,
                )
                appointment = await unit_of_work.appointments.add(
                    Appointment(
                        client_id=client.id,
                        window_id=window.id,
                        service_id=service.id,
                        service_name_snapshot=service.name,
                        price_snapshot=service.price,
                        duration_min_snapshot=service.duration_min_minutes,
                        duration_max_snapshot=service.duration_max_minutes,
                        status=AppointmentStatus.CONFIRMED,
                        client_comment=values.client_comment,
                    )
                )
                window.status = AvailabilityWindowStatus.BOOKED
                await unit_of_work.appointments.add_history(
                    AppointmentStatusHistory(
                        appointment_id=appointment.id,
                        previous_status=None,
                        new_status=AppointmentStatus.CONFIRMED,
                        changed_by_user_id=client.id,
                        reason=None,
                    )
                )

                admin_users = await unit_of_work.users.list_by_telegram_ids(
                    self._admin_telegram_ids
                )
                schedules = future_reminder_schedules(
                    start_at=window.start_at,
                    now=current_time,
                    offsets_minutes=settings.reminder_offsets_minutes,
                    client_user_id=client.id,
                    admin_user_ids=[user.id for user in admin_users if not user.is_blocked],
                )
                await unit_of_work.notifications.add_all(
                    [
                        NotificationJob(
                            appointment_id=appointment.id,
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
                    actor_user_id=client.id,
                    action="appointment.created",
                    entity_type="appointment",
                    entity_id=str(appointment.id),
                    changes={
                        "window_id": window.id,
                        "service_id": service.id,
                        "status": AppointmentStatus.CONFIRMED.value,
                        "has_client_comment": values.client_comment is not None,
                    },
                    correlation_id=correlation_id,
                )
                await unit_of_work.commit()
                return BookingReceipt(
                    appointment_id=appointment.id,
                    service_name=appointment.service_name_snapshot,
                    price=appointment.price_snapshot,
                    duration_min_minutes=appointment.duration_min_snapshot,
                    duration_max_minutes=appointment.duration_max_snapshot,
                    start_at=window.start_at,
                    end_at=window.end_at,
                    timezone=settings.timezone,
                    address=settings.address,
                    map_url=settings.map_url,
                    master_telegram_url=settings.master_telegram_url,
                    client_name=values.client_name,
                    phone=values.phone,
                )
        except IntegrityError as exc:
            raise BookingConflictError(_WINDOW_TAKEN_MESSAGE) from exc

    @staticmethod
    async def _consented_client(
        unit_of_work: SqlAlchemyUnitOfWork,
        telegram_id: int,
        *,
        for_update: bool = False,
    ) -> User:
        user = await unit_of_work.users.get_by_telegram_id(
            telegram_id,
            for_update=for_update,
        )
        if user is None or user.privacy_consent_at is None:
            raise PrivacyConsentRequiredError(
                "Сначала примите условия обработки данных через команду /start."
            )
        return user

    @staticmethod
    async def _settings(unit_of_work: SqlAlchemyUnitOfWork) -> BusinessSettings:
        settings = await unit_of_work.settings.get()
        if settings is None:
            raise RuntimeError("Business settings row is missing")
        return settings

    @staticmethod
    def _active_service(service: Service | None) -> Service:
        if service is None or not service.is_active:
            raise BookingUnavailableError("Эта услуга сейчас недоступна для записи.")
        return service

    def _ensure_privacy_policy(self) -> None:
        if not self._privacy_policy_configured:
            raise PrivacyConsentRequiredError(
                "Онлайн-запись временно недоступна: политика конфиденциальности не настроена."
            )

    @staticmethod
    def _service_fits(service: Service, start_at: datetime, end_at: datetime) -> bool:
        return service.duration_max_minutes * 60 <= (end_at - start_at).total_seconds()

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
