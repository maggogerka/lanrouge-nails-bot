"""Client availability queries and transactional appointment creation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.database.models import (
    Appointment,
    AppointmentAddonSnapshot,
    AppointmentReferenceMedia,
    AppointmentStatusHistory,
    BusinessSettings,
    NotificationJob,
    Payment,
    Service,
    ServiceAddon,
    User,
)
from app.database.models.commerce import BusinessPaymentSettings
from app.domain.availability import utc_day_bounds
from app.domain.booking import validate_bookable_date, validate_service_fits_window
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    ManualPaymentStatus,
    NotificationType,
    PaymentMode,
    PaymentStatus,
    PortfolioStatus,
    ReservationStatus,
)
from app.domain.errors import (
    AuthorizationError,
    BookingConflictError,
    BookingLimitError,
    BookingUnavailableError,
    FutureBookingLimitError,
    PrivacyConsentRequiredError,
)
from app.domain.notifications import future_reminder_schedules
from app.domain.payments import PaymentStateError, PaymentType
from app.domain.reference_retention import ReferenceRetentionPolicy
from app.domain.reservations import ReservationStateError, ReservationToken
from app.domain.service_offering import (
    BaseServiceTerms,
    EffectiveServiceTerms,
    StaffServiceOverrides,
    resolve_service_terms,
)
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.payments.providers.base import PaymentProviderError
from app.payments.providers.manual import ManualPaymentProvider
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.booking import (
    BookableMasterView,
    BookingAvailability,
    BookingMasterOptions,
    BookingReceipt,
    BookingRequest,
    BookingWindowView,
    BusinessInfo,
    ClientActor,
    ReferenceMediaPolicy,
    ReferenceMediaView,
)
from app.schemas.features import FeatureName
from app.schemas.payment import PaymentCreate, PaymentView
from app.schemas.reservation import ReservationCreate
from app.schemas.service import AdminActor, AppointmentAddonView, ServiceAddonView, ServiceView
from app.security import LEGACY_ADMIN_ROLES
from app.services.appointment_common import ensure_admin
from app.services.authorization_service import AuthorizationService
from app.services.feature_guard import require_feature
from app.services.payment_service import PaymentService
from app.services.reservation_service import ReservationService
from app.services.subscription_service import SubscriptionService

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]

_WINDOW_TAKEN_MESSAGE = "К сожалению, это время только что заняли. Выберите другое свободное окно."


@dataclass(frozen=True, slots=True)
class _CheckoutResult:
    receipt: BookingReceipt
    payment_values: PaymentCreate | None = None


@dataclass(frozen=True, slots=True)
class _BookingLimitOverride:
    actor_user_id: int
    reason: str


class BookingService:
    """Expose safe availability and atomically occupy exactly one window."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
        *,
        reference_retention_policy: ReferenceRetentionPolicy | None = None,
        payment_services: Mapping[PaymentMode, PaymentService] | None = None,
        payment_return_url: str | None = None,
        subscription_service: SubscriptionService | None = None,
        authorization_service: AuthorizationService | None = None,
        business_id: int = DEFAULT_BUSINESS_ID,
    ) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids
        self._reference_retention_policy = reference_retention_policy or ReferenceRetentionPolicy()
        self._payment_services = dict(
            payment_services or {PaymentMode.MANUAL: PaymentService(ManualPaymentProvider())}
        )
        self._payment_return_url = payment_return_url
        self._subscription_service = subscription_service
        self._authorization_service = authorization_service
        self._business_id = business_id

    async def list_active_services(self, actor: ClientActor) -> list[ServiceView]:
        async with self._unit_of_work_factory() as unit_of_work:
            await self._consented_client(unit_of_work, actor.telegram_id)
            services = await unit_of_work.services.list_active()
            return [ServiceView.model_validate(service) for service in services]

    async def list_active_services_for_master(
        self,
        actor: ClientActor,
        staff_member_id: int,
    ) -> list[ServiceView]:
        """Return public services that are currently bookable with one master."""

        if staff_member_id <= 0:
            raise BookingUnavailableError("Мастер больше недоступен для записи.")
        async with self._unit_of_work_factory() as unit_of_work:
            await require_feature(unit_of_work, FeatureName.ONLINE_BOOKING)
            await self._consented_client(unit_of_work, actor.telegram_id)
            rows = await unit_of_work.service_assignments.list_bookable_services_for_staff(
                unit_of_work.business_id,
                staff_member_id,
            )
            return [ServiceView.model_validate(service) for _, service in rows]

    async def list_bookable_masters(
        self,
        actor: ClientActor,
        service_id: int,
    ) -> BookingMasterOptions:
        """Return current service assignments and the central selection switch."""

        async with self._unit_of_work_factory() as unit_of_work:
            await require_feature(unit_of_work, FeatureName.ONLINE_BOOKING)
            await self._consented_client(unit_of_work, actor.telegram_id)
            self._active_service(await unit_of_work.services.get(service_id))
            flags = await unit_of_work.features.get()
            if flags is None:
                raise RuntimeError("Business feature settings are missing")
            rows = await unit_of_work.service_assignments.list_bookable_assignments(
                unit_of_work.business_id,
                service_id,
            )
            seen: set[int] = set()
            masters: list[BookableMasterView] = []
            for _, _, master in rows:
                if master.id in seen:
                    continue
                seen.add(master.id)
                masters.append(
                    BookableMasterView(
                        id=master.id,
                        display_name=master.display_name,
                        specialization=master.specialization,
                        telegram_photo_file_id=master.telegram_photo_file_id,
                    )
                )
            return BookingMasterOptions(
                selection_enabled=bool(flags.master_selection),
                masters=masters,
            )

    async def list_service_addons(
        self, actor: ClientActor, service_id: int
    ) -> list[ServiceAddonView]:
        """Return only currently active additions for the selected base service."""

        async with self._unit_of_work_factory() as unit_of_work:
            await require_feature(unit_of_work, FeatureName.ONLINE_BOOKING)
            await self._consented_client(unit_of_work, actor.telegram_id)
            self._active_service(await unit_of_work.services.get(service_id))
            rows = await unit_of_work.service_addons.list_active(service_id)
            return [ServiceAddonView.model_validate(row) for row in rows]

    async def get_business_info(self, actor: ClientActor) -> BusinessInfo:
        async with self._unit_of_work_factory() as unit_of_work:
            await self._consented_client(unit_of_work, actor.telegram_id)
            settings = await self._settings(unit_of_work)
            return BusinessInfo(
                business_name=settings.business_name,
                address=settings.address,
                map_url=settings.map_url,
                master_telegram_url=settings.master_telegram_url,
            )

    async def get_reference_media_policy(self, actor: ClientActor) -> ReferenceMediaPolicy:
        async with self._unit_of_work_factory() as unit_of_work:
            await require_feature(unit_of_work, FeatureName.REFERENCE_PHOTOS)
            await self._consented_client(unit_of_work, actor.telegram_id)
            settings = await self._settings(unit_of_work)
            return ReferenceMediaPolicy(
                max_media=settings.booking_reference_max_media,
                edit_deadline_hours=settings.booking_reference_edit_deadline_hours,
            )

    async def list_availability(
        self,
        actor: ClientActor,
        service_id: int,
        *,
        addon_ids: Sequence[int] = (),
        staff_member_id: int | None = None,
        local_date: date | None = None,
        now: datetime | None = None,
    ) -> BookingAvailability:
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            await require_feature(unit_of_work, FeatureName.ONLINE_BOOKING)
            await self._consented_client(unit_of_work, actor.telegram_id)
            settings = await self._settings(unit_of_work)
            service = self._active_service(await unit_of_work.services.get(service_id))
            addons = await self._selected_addons(
                unit_of_work,
                service.id,
                addon_ids,
                for_update=False,
            )
            assignment_rows = await unit_of_work.service_assignments.list_bookable_assignments(
                unit_of_work.business_id,
                service.id,
            )
            eligible_masters = {master.id: master for _, _, master in assignment_rows}
            eligible_assignments = {
                master.id: assignment for assignment, _, master in assignment_rows
            }
            if staff_member_id is not None and staff_member_id not in eligible_masters:
                raise BookingUnavailableError("Selected master is not available")

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
                if window.staff_member_id not in eligible_masters:
                    continue
                master_pause = getattr(
                    eligible_masters[window.staff_member_id],
                    "schedule_paused_until",
                    None,
                )
                if master_pause is not None and window.start_at < master_pause:
                    continue
                if staff_member_id is not None and window.staff_member_id != staff_member_id:
                    continue
                candidate_date = window.start_at.astimezone(zone).date()
                if local_date is not None and candidate_date != local_date:
                    continue
                if candidate_date.weekday() == 5 and not settings.allow_saturday:
                    continue
                if candidate_date.weekday() == 6 and not settings.allow_sunday:
                    continue
                terms = self._terms_with_addons(
                    self._effective_terms(
                        service,
                        eligible_assignments[window.staff_member_id],
                    ),
                    addons,
                )
                if (
                    terms.duration_max_minutes * 60
                    <= (window.end_at - window.start_at).total_seconds()
                ):
                    candidates.append(window)

            available = []
            counts: dict[tuple[date, int], int] = {}
            for window in candidates:
                candidate_date = window.start_at.astimezone(zone).date()
                capacity_key = (candidate_date, window.staff_member_id)
                if capacity_key not in counts:
                    day_start, day_end = utc_day_bounds(candidate_date, settings.timezone)
                    counts[capacity_key] = await unit_of_work.appointments.count_capacity_between(
                        day_start,
                        day_end,
                        staff_member_id=window.staff_member_id,
                    )
                if counts[capacity_key] < settings.max_appointments_per_day:
                    base_terms = self._effective_terms(
                        service, eligible_assignments[window.staff_member_id]
                    )
                    terms = self._terms_with_addons(base_terms, addons)
                    available.append(
                        BookingWindowView(
                            id=window.id,
                            start_at=window.start_at,
                            end_at=window.end_at,
                            timezone=settings.timezone,
                            staff_member_id=window.staff_member_id,
                            master_name=eligible_masters[window.staff_member_id].display_name,
                            price=terms.price,
                            duration_min_minutes=terms.duration_min_minutes,
                            duration_max_minutes=terms.duration_max_minutes,
                            prepayment_amount=self._prepayment_amount(terms),
                            base_price=base_terms.price,
                            addons_price=sum((addon.price for addon in addons), Decimal("0.00")),
                        )
                    )

            return BookingAvailability(
                service=ServiceView.model_validate(service),
                selected_addons=[ServiceAddonView.model_validate(addon) for addon in addons],
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
        allow_self_booking_blocked: bool = False,
        _booking_limit_override: _BookingLimitOverride | None = None,
    ) -> BookingReceipt:
        current_time = self._aware_now(now)
        existing = await self._existing_checkout(actor, values)
        if existing is not None:
            return await self._finalize_checkout(existing, now=current_time)
        if self._subscription_service is not None:
            await self._subscription_service.ensure_new_bookings_allowed(
                self._business_id,
                now=current_time,
            )
        checkout: _CheckoutResult | None = None
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                await require_feature(unit_of_work, FeatureName.ONLINE_BOOKING)
                if values.reference_media:
                    await require_feature(unit_of_work, FeatureName.REFERENCE_PHOTOS)
                settings = await self._settings(unit_of_work)
                initial_window = await unit_of_work.windows.get(values.window_id)
                if initial_window is None or initial_window.business_id != unit_of_work.business_id:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                local_date = initial_window.start_at.astimezone(ZoneInfo(settings.timezone)).date()
                await unit_of_work.windows.lock_local_date(
                    local_date, staff_member_id=initial_window.staff_member_id
                )

                window = await unit_of_work.windows.get(values.window_id, for_update=True)
                if window is None or window.status is not AvailabilityWindowStatus.OPEN:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                if window.business_id != unit_of_work.business_id:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                if (
                    values.staff_member_id is not None
                    and window.staff_member_id != values.staff_member_id
                ):
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                client = await self._consented_client(
                    unit_of_work,
                    actor.telegram_id,
                    for_update=True,
                    allow_self_booking_blocked=allow_self_booking_blocked,
                )
                await self._enforce_future_booking_limit(
                    unit_of_work,
                    settings,
                    client.id,
                    current_time,
                    override=_booking_limit_override,
                    correlation_id=correlation_id,
                )
                service = self._active_service(
                    await unit_of_work.services.get(values.service_id, for_update=True)
                )
                if service.business_id != unit_of_work.business_id:
                    raise BookingUnavailableError("Service is not available")
                master = await unit_of_work.staff.get_by_id(
                    unit_of_work.business_id,
                    window.staff_member_id,
                    for_update=True,
                )
                assignment = await unit_of_work.service_assignments.get_assignment(
                    unit_of_work.business_id,
                    window.staff_member_id,
                    service.id,
                    for_update=True,
                )
                if (
                    master is None
                    or not master.is_active
                    or not master.is_bookable
                    or assignment is None
                    or not assignment.is_active
                    or not assignment.online_booking_enabled
                ):
                    raise BookingUnavailableError("Selected master is not available")
                if (
                    master.schedule_paused_until is not None
                    and window.start_at < master.schedule_paused_until
                ):
                    raise BookingUnavailableError("Selected master is temporarily unavailable")
                addons = await self._selected_addons(
                    unit_of_work,
                    service.id,
                    values.addon_ids,
                    for_update=True,
                )
                base_terms = self._effective_terms(service, assignment)
                terms = self._terms_with_addons(base_terms, addons)
                if not terms.online_booking_enabled:
                    raise BookingUnavailableError("Selected service is not available")
                design = None
                if values.design_reference_id is not None:
                    design = await unit_of_work.portfolio.get(
                        values.design_reference_id,
                        for_update=True,
                    )
                    if design is None or design.status is not PortfolioStatus.PUBLISHED:
                        raise BookingUnavailableError(
                            "Выбранная работа больше недоступна. Выберите другой дизайн."
                        )
                    if (
                        design.linked_service_id is not None
                        and design.linked_service_id != service.id
                    ):
                        raise BookingUnavailableError(
                            "Этот дизайн связан с другой услугой. Начните выбор заново."
                        )

                validated_local_date = validate_bookable_date(
                    start_at=window.start_at,
                    now=current_time,
                    timezone=settings.timezone,
                    booking_horizon_days=settings.booking_horizon_days,
                    allow_saturday=settings.allow_saturday,
                    allow_sunday=settings.allow_sunday,
                )
                validate_service_fits_window(
                    duration_max_minutes=terms.duration_max_minutes,
                    start_at=window.start_at,
                    end_at=window.end_at,
                )
                actual_local_date = window.start_at.astimezone(ZoneInfo(settings.timezone)).date()
                if actual_local_date != local_date or validated_local_date != local_date:
                    raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
                day_start, day_end = utc_day_bounds(local_date, settings.timezone)
                daily_count = await unit_of_work.appointments.count_capacity_between(
                    day_start,
                    day_end,
                    staff_member_id=window.staff_member_id,
                )
                if daily_count >= settings.max_appointments_per_day:
                    raise BookingLimitError("На эту дату больше нет мест.")
                if len(values.reference_media) > settings.booking_reference_max_media:
                    raise BookingUnavailableError(
                        "Превышено допустимое количество фотографий-референсов."
                    )
                unique_reference_ids = {
                    item.telegram_file_unique_id for item in values.reference_media
                }
                if len(unique_reference_ids) != len(values.reference_media):
                    raise BookingUnavailableError(
                        "Одинаковые фотографии нельзя добавлять повторно."
                    )

                await unit_of_work.users.update_booking_profile(
                    client,
                    name=values.client_name,
                    phone=values.phone,
                )
                payment_settings = await unit_of_work.reservations.payment_settings()
                payment_mode, prepayment_amount = await self._payment_policy(
                    unit_of_work,
                    payment_settings,
                    terms,
                )
                appointment_status = self._initial_appointment_status(payment_mode)
                appointment = await unit_of_work.appointments.add(
                    Appointment(
                        business_id=unit_of_work.business_id,
                        staff_member_id=window.staff_member_id,
                        client_id=client.id,
                        window_id=window.id,
                        service_id=service.id,
                        design_reference_id=design.id if design is not None else None,
                        design_title_snapshot=design.title if design is not None else None,
                        service_name_snapshot=service.name,
                        master_name_snapshot=master.display_name,
                        price_snapshot=terms.price,
                        prepayment_snapshot=prepayment_amount,
                        currency_snapshot="RUB",
                        payment_mode_snapshot=payment_mode,
                        duration_min_snapshot=terms.duration_min_minutes,
                        duration_max_snapshot=terms.duration_max_minutes,
                        scheduled_start_at=window.start_at,
                        scheduled_end_at=window.end_at,
                        status=appointment_status,
                        client_comment=values.client_comment,
                    )
                )
                addon_snapshot_rows = [
                    AppointmentAddonSnapshot(
                        business_id=unit_of_work.business_id,
                        appointment_id=appointment.id,
                        service_addon_id=addon.id,
                        name_snapshot=addon.name,
                        description_snapshot=addon.description,
                        price_snapshot=addon.price,
                        duration_min_snapshot=addon.duration_min_minutes,
                        duration_max_snapshot=addon.duration_max_minutes,
                        position=position,
                    )
                    for position, addon in enumerate(addons)
                ]
                if addon_snapshot_rows:
                    await unit_of_work.service_addons.add_snapshots(addon_snapshot_rows)
                reference_rows = [
                    AppointmentReferenceMedia(
                        business_id=unit_of_work.business_id,
                        appointment_id=appointment.id,
                        telegram_file_id=item.telegram_file_id,
                        telegram_file_unique_id=item.telegram_file_unique_id,
                        media_type=item.media_type,
                        position=position,
                        uploaded_by_user_id=client.id,
                        expires_at=self._reference_retention_policy.expires_at(
                            status=appointment_status,
                            planned_end_at=window.end_at,
                        ),
                    )
                    for position, item in enumerate(values.reference_media)
                ]
                if reference_rows:
                    await unit_of_work.reference_media.add_all(reference_rows)
                payment_values: PaymentCreate | None = None
                reservation_expires_at: datetime | None = None
                payment: Payment | None = None
                if payment_mode is PaymentMode.DISABLED:
                    window.status = AvailabilityWindowStatus.BOOKED
                else:
                    if payment_settings is None:
                        raise RuntimeError("payment settings are missing for an enabled mode")
                    token = ReservationToken.from_raw(values.reservation_token.get_secret_value())
                    reservation, _ = await ReservationService(
                        unit_of_work.reservations,
                        unit_of_work.payments,
                        unit_of_work.audit,
                    ).create(
                        ReservationCreate(
                            business_id=unit_of_work.business_id,
                            client_id=client.id,
                            staff_member_id=window.staff_member_id,
                            window_id=window.id,
                            service_id=service.id,
                            appointment_id=appointment.id,
                            idempotency_key=values.checkout_idempotency_key,
                            ttl_minutes=payment_settings.reservation_ttl_minutes,
                            correlation_id=correlation_id,
                        ),
                        token,
                        now=current_time,
                    )
                    reservation_expires_at = reservation.expires_at
                    payment_values = PaymentCreate(
                        business_id=unit_of_work.business_id,
                        appointment_id=appointment.id,
                        provider=payment_mode,
                        payment_type=(
                            PaymentType.FULL_PAYMENT
                            if prepayment_amount >= terms.price
                            else PaymentType.DEPOSIT
                        ),
                        amount=prepayment_amount,
                        currency="RUB",
                        idempotency_key=f"pay:{values.checkout_idempotency_key}",
                        safe_metadata={"reservation_id": str(reservation.id)},
                        correlation_id=correlation_id,
                        return_url=(
                            self._payment_return_url
                            if payment_mode is PaymentMode.YOOKASSA
                            else None
                        ),
                        description=f"Предоплата: {service.name}"[:128],
                    )
                    payment_service = self._required_payment_service(payment_mode)
                    payment = payment_service.new_payment(
                        payment_values,
                        expires_at=reservation.expires_at,
                    )
                    if payment_mode is PaymentMode.MANUAL:
                        payment.manual_status = ManualPaymentStatus.AWAITING_PAYMENT
                    payment.provider_account_ref = payment_settings.provider_account_ref
                    await unit_of_work.payments.add(payment)
                await unit_of_work.appointments.add_history(
                    AppointmentStatusHistory(
                        appointment_id=appointment.id,
                        previous_status=None,
                        new_status=appointment_status,
                        changed_by_user_id=client.id,
                        reason=None,
                    )
                )

                staff_rows = await unit_of_work.staff.list_active_by_roles(
                    unit_of_work.business_id,
                    LEGACY_ADMIN_ROLES,
                )
                schedules = future_reminder_schedules(
                    start_at=window.start_at,
                    now=current_time,
                    offsets_minutes=settings.reminder_offsets_minutes,
                    client_user_id=client.id,
                    admin_user_ids=[user.id for _, user in staff_rows if not user.is_blocked],
                )
                notification_jobs = [
                    NotificationJob(
                        business_id=unit_of_work.business_id,
                        appointment_id=appointment.id,
                        recipient_user_id=schedule.recipient_user_id,
                        notification_type=schedule.notification_type,
                        offset_minutes=schedule.offset_minutes,
                        scheduled_at=schedule.scheduled_at,
                        available_at=schedule.scheduled_at,
                    )
                    for schedule in schedules
                ]
                if (
                    payment_mode is PaymentMode.MANUAL
                    and payment_settings is not None
                    and getattr(payment_settings, "client_payment_reminders_enabled", True)
                ):
                    raw_offsets = getattr(
                        payment_settings, "client_payment_reminder_minutes", None
                    ) or [5, 10]
                    offsets = sorted(
                        {
                            int(offset)
                            for offset in raw_offsets
                            if isinstance(offset, int)
                            and 0 < offset < payment_settings.reservation_ttl_minutes
                        }
                    )
                    notification_jobs.extend(
                        NotificationJob(
                            business_id=unit_of_work.business_id,
                            appointment_id=appointment.id,
                            recipient_user_id=client.id,
                            notification_type=NotificationType.PAYMENT_DUE_CLIENT,
                            offset_minutes=offset,
                            scheduled_at=current_time + timedelta(minutes=offset),
                            available_at=current_time + timedelta(minutes=offset),
                        )
                        for offset in offsets
                    )
                await unit_of_work.notifications.add_all(notification_jobs)
                await unit_of_work.audit.add(
                    actor_user_id=client.id,
                    action="appointment.created",
                    entity_type="appointment",
                    entity_id=str(appointment.id),
                    changes={
                        "window_id": window.id,
                        "service_id": service.id,
                        "status": appointment_status.value,
                        "has_client_comment": values.client_comment is not None,
                        "design_reference_id": design.id if design is not None else None,
                        "reference_media_count": len(reference_rows),
                        "addon_count": len(addon_snapshot_rows),
                    },
                    correlation_id=correlation_id,
                )
                booked_waitlist_entries = await unit_of_work.waitlist.mark_booked_for_window(
                    client_id=client.id,
                    service_id=service.id,
                    window_id=window.id,
                    appointment_id=appointment.id,
                )
                if booked_waitlist_entries:
                    await unit_of_work.audit.add(
                        actor_user_id=client.id,
                        action="waitlist.booked",
                        entity_type="appointment",
                        entity_id=str(appointment.id),
                        changes={"entry_ids": booked_waitlist_entries},
                        correlation_id=correlation_id,
                    )
                await unit_of_work.commit()
                checkout = _CheckoutResult(
                    receipt=BookingReceipt(
                        appointment_id=appointment.id,
                        service_name=appointment.service_name_snapshot,
                        base_price=base_terms.price,
                        addons=[
                            AppointmentAddonView.model_validate(row) for row in addon_snapshot_rows
                        ],
                        master_name=appointment.master_name_snapshot,
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
                        design_title=appointment.design_title_snapshot,
                        reference_media=[
                            ReferenceMediaView(
                                id=row.id,
                                telegram_file_id=row.telegram_file_id,
                                telegram_file_unique_id=row.telegram_file_unique_id,
                                media_type=row.media_type,
                                position=row.position,
                            )
                            for row in reference_rows
                        ],
                        appointment_status=appointment_status,
                        payment_mode=payment_mode,
                        payment_id=payment.id if payment is not None else None,
                        payment_status=payment.status if payment is not None else None,
                        payment_amount=payment.amount if payment is not None else None,
                        payment_currency=payment.currency if payment is not None else None,
                        reservation_expires_at=reservation_expires_at,
                        manual_payment_instructions=(
                            payment_settings.manual_payment_instructions
                            if payment_settings is not None and payment_mode is PaymentMode.MANUAL
                            else None
                        ),
                    ),
                    payment_values=payment_values,
                )
        except IntegrityError as exc:
            raise BookingConflictError(_WINDOW_TAKEN_MESSAGE) from exc
        except (PaymentStateError, ReservationStateError) as exc:
            raise BookingUnavailableError(str(exc)) from exc
        if checkout is None:
            raise RuntimeError("booking checkout did not produce a result")
        return await self._finalize_checkout(checkout, now=current_time)

    async def _existing_checkout(
        self,
        actor: ClientActor,
        values: BookingRequest,
    ) -> _CheckoutResult | None:
        """Resume a payment retry without creating another appointment or hold."""

        async with self._unit_of_work_factory() as unit_of_work:
            reservation = await unit_of_work.reservations.get_by_idempotency_key(
                values.checkout_idempotency_key
            )
            if reservation is None:
                return None
            client = await self._consented_client(unit_of_work, actor.telegram_id)
            token = ReservationToken.from_raw(values.reservation_token.get_secret_value())
            expected_staff_id = values.staff_member_id or reservation.staff_member_id
            if (
                reservation.client_id != client.id
                or reservation.window_id != values.window_id
                or reservation.service_id != values.service_id
                or reservation.staff_member_id != expected_staff_id
                or reservation.token_digest != token.digest
                or reservation.appointment_id is None
                or reservation.status in {ReservationStatus.EXPIRED, ReservationStatus.CANCELLED}
            ):
                raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
            appointment = await unit_of_work.appointments.get(reservation.appointment_id)
            window = await unit_of_work.windows.get(reservation.window_id)
            payment = await unit_of_work.payments.get_latest_for_appointment(
                reservation.appointment_id
            )
            settings = await self._settings(unit_of_work)
            payment_settings = await unit_of_work.reservations.payment_settings()
            if (
                appointment is None
                or window is None
                or payment is None
                or appointment.client_id != client.id
                or appointment.payment_mode_snapshot is PaymentMode.DISABLED
                or payment.provider is not appointment.payment_mode_snapshot
            ):
                raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
            references = await unit_of_work.reference_media.list_active(appointment.id)
            addon_snapshots = await unit_of_work.service_addons.list_snapshots(appointment.id)
            if sorted(values.addon_ids) != sorted(row.service_addon_id for row in addon_snapshots):
                raise BookingConflictError(_WINDOW_TAKEN_MESSAGE)
            safe_metadata = {
                key: value
                for key, value in payment.safe_metadata.items()
                if key not in {"business_id", "appointment_id"}
            }
            payment_values = PaymentCreate(
                business_id=payment.business_id,
                appointment_id=payment.appointment_id,
                provider=payment.provider,
                payment_type=payment.payment_type,
                amount=payment.amount,
                currency=payment.currency,
                idempotency_key=payment.idempotency_key,
                safe_metadata=safe_metadata,
                correlation_id=payment.correlation_id,
                return_url=(
                    self._payment_return_url if payment.provider is PaymentMode.YOOKASSA else None
                ),
                description=f"Предоплата: {appointment.service_name_snapshot}"[:128],
            )
            return _CheckoutResult(
                receipt=BookingReceipt(
                    appointment_id=appointment.id,
                    service_name=appointment.service_name_snapshot,
                    base_price=appointment.price_snapshot
                    - sum(
                        (row.price_snapshot for row in addon_snapshots),
                        Decimal("0.00"),
                    ),
                    addons=[AppointmentAddonView.model_validate(row) for row in addon_snapshots],
                    master_name=appointment.master_name_snapshot,
                    price=appointment.price_snapshot,
                    duration_min_minutes=appointment.duration_min_snapshot,
                    duration_max_minutes=appointment.duration_max_snapshot,
                    start_at=window.start_at,
                    end_at=window.end_at,
                    timezone=settings.timezone,
                    address=settings.address,
                    map_url=settings.map_url,
                    master_telegram_url=settings.master_telegram_url,
                    client_name=client.first_name or values.client_name,
                    phone=client.phone or values.phone,
                    design_title=appointment.design_title_snapshot,
                    reference_media=[ReferenceMediaView.model_validate(row) for row in references],
                    appointment_status=appointment.status,
                    payment_mode=payment.provider,
                    payment_id=payment.id,
                    payment_status=payment.status,
                    payment_amount=payment.amount,
                    payment_currency=payment.currency,
                    payment_confirmation_url=payment.confirmation_url,
                    reservation_expires_at=appointment.reservation_expires_at,
                    manual_payment_instructions=(
                        payment_settings.manual_payment_instructions
                        if payment_settings is not None and payment.provider is PaymentMode.MANUAL
                        else None
                    ),
                ),
                payment_values=payment_values,
            )

    async def _finalize_checkout(
        self,
        checkout: _CheckoutResult,
        *,
        now: datetime,
    ) -> BookingReceipt:
        values = checkout.payment_values
        if values is None or checkout.receipt.payment_id is None:
            return checkout.receipt
        payment = await self._submit_payment(
            checkout.receipt.payment_id,
            values,
            now=now,
        )
        appointment_status = checkout.receipt.appointment_status
        reservation_expires_at = checkout.receipt.reservation_expires_at
        if payment.status is PaymentStatus.SUCCEEDED:
            await self._consume_paid_reservation(payment, now=now)
            appointment_status = AppointmentStatus.CONFIRMED
            reservation_expires_at = None
        return checkout.receipt.model_copy(
            update={
                "appointment_status": appointment_status,
                "payment_status": payment.status,
                "payment_confirmation_url": payment.confirmation_url,
                "reservation_expires_at": reservation_expires_at,
            }
        )

    async def _submit_payment(
        self,
        payment_id: int,
        values: PaymentCreate,
        *,
        now: datetime,
    ) -> PaymentView:
        """Call a provider outside DB transactions, then lock and persist its safe projection."""

        service = self._required_payment_service(values.provider)
        async with self._unit_of_work_factory() as unit_of_work:
            payment = await unit_of_work.payments.get(payment_id)
            if payment is None:
                raise BookingUnavailableError("Платёж не найден.")
            service.require_same_intent(payment, values)
            if payment.status is not PaymentStatus.CREATED:
                return PaymentView.model_validate(payment)
            detached = service.new_payment(values, expires_at=payment.expires_at)
            detached.id = payment.id
            detached.provider_account_ref = payment.provider_account_ref

        try:
            provider_result = await service.create_with_provider(detached, values, now=now)
        except PaymentProviderError as exc:
            async with self._unit_of_work_factory() as unit_of_work:
                payment = await unit_of_work.payments.get(payment_id, for_update=True)
                if payment is not None and payment.status is PaymentStatus.CREATED:
                    service.require_same_intent(payment, values)
                    service.apply_creation_failure(payment, exc)
                    await unit_of_work.audit.add(
                        actor_user_id=None,
                        action="payment.creation_failed",
                        entity_type="payment",
                        entity_id=str(payment_id),
                        changes={"error_code": exc.code, "retryable": exc.retryable},
                        correlation_id=values.correlation_id,
                    )
                    await unit_of_work.commit()
            raise BookingUnavailableError(
                "Платёжный сервис временно недоступен. Время сохранено в резерве; "
                "повторите подтверждение до истечения таймера."
            ) from None

        async with self._unit_of_work_factory() as unit_of_work:
            payment = await unit_of_work.payments.get(payment_id, for_update=True)
            if payment is None:
                raise BookingUnavailableError("Платёж не найден.")
            service.require_same_intent(payment, values)
            if payment.status is PaymentStatus.CREATED:
                service.apply_creation_result(payment, provider_result, now=now)
                await unit_of_work.audit.add(
                    actor_user_id=None,
                    action="payment.created_at_provider",
                    entity_type="payment",
                    entity_id=str(payment.id),
                    changes={
                        "provider": payment.provider.value,
                        "status": payment.status.value,
                        "appointment_id": payment.appointment_id,
                    },
                    correlation_id=values.correlation_id,
                )
                await unit_of_work.commit()
            return PaymentView.model_validate(payment)

    async def _consume_paid_reservation(
        self,
        payment: PaymentView,
        *,
        now: datetime,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            reservation = await unit_of_work.reservations.get_active_for_appointment(
                payment.appointment_id,
                for_update=True,
            )
            if reservation is None:
                return
            await ReservationService(
                unit_of_work.reservations,
                unit_of_work.payments,
                unit_of_work.audit,
            ).consume(
                reservation.id,
                appointment_id=payment.appointment_id,
                actor_user_id=None,
                now=now,
                correlation_id=None,
            )
            await unit_of_work.commit()

    async def _payment_policy(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        settings: BusinessPaymentSettings | None,
        terms: EffectiveServiceTerms,
    ) -> tuple[PaymentMode, Decimal]:
        amount = self._prepayment_amount(terms)
        if settings is None or settings.mode is PaymentMode.DISABLED or amount <= 0:
            return PaymentMode.DISABLED, Decimal("0.00")
        if amount > terms.price:
            raise BookingUnavailableError("Предоплата не может превышать стоимость услуги.")
        await require_feature(unit_of_work, FeatureName.PREPAYMENT)
        required_feature = (
            FeatureName.MANUAL_PAYMENTS
            if settings.mode is PaymentMode.MANUAL
            else FeatureName.YOOKASSA_PAYMENTS
        )
        await require_feature(unit_of_work, required_feature)
        self._required_payment_service(settings.mode)
        if (
            settings.mode is PaymentMode.MANUAL
            and not (settings.manual_payment_instructions or "").strip()
        ):
            raise BookingUnavailableError("Инструкции для ручной оплаты ещё не настроены.")
        if settings.mode is PaymentMode.YOOKASSA and self._payment_return_url is None:
            raise BookingUnavailableError("YooKassa ещё не настроена владельцем бизнеса.")
        return settings.mode, amount

    @staticmethod
    def _initial_appointment_status(mode: PaymentMode) -> AppointmentStatus:
        if mode in {PaymentMode.MANUAL, PaymentMode.YOOKASSA}:
            return AppointmentStatus.PENDING_PAYMENT
        return AppointmentStatus.CONFIRMED

    @staticmethod
    def _prepayment_amount(terms: EffectiveServiceTerms) -> Decimal:
        if terms.prepayment_amount is not None:
            return terms.prepayment_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if terms.prepayment_percent is not None:
            return (terms.price * terms.prepayment_percent / Decimal("100")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        return Decimal("0.00")

    @staticmethod
    def _effective_terms(service: Service, assignment: object) -> EffectiveServiceTerms:
        return resolve_service_terms(
            BaseServiceTerms(
                price=service.price,
                duration_min_minutes=service.duration_min_minutes,
                duration_max_minutes=service.duration_max_minutes,
                prepayment_amount=service.prepayment_amount,
                online_booking_enabled=service.is_active,
            ),
            StaffServiceOverrides(
                price=getattr(assignment, "price_override", None),
                duration_min_minutes=getattr(
                    assignment,
                    "duration_min_minutes_override",
                    None,
                ),
                duration_max_minutes=getattr(
                    assignment,
                    "duration_max_minutes_override",
                    None,
                ),
                prepayment_amount=getattr(assignment, "prepayment_amount_override", None),
                prepayment_percent=getattr(assignment, "prepayment_percent_override", None),
                online_booking_enabled=bool(getattr(assignment, "online_booking_enabled", False)),
                is_active=bool(getattr(assignment, "is_active", False)),
            ),
        )

    @staticmethod
    def _terms_with_addons(
        terms: EffectiveServiceTerms,
        addons: Sequence[ServiceAddon],
    ) -> EffectiveServiceTerms:
        return EffectiveServiceTerms(
            price=terms.price + sum((addon.price for addon in addons), Decimal("0.00")),
            duration_min_minutes=terms.duration_min_minutes
            + sum(addon.duration_min_minutes for addon in addons),
            duration_max_minutes=terms.duration_max_minutes
            + sum(addon.duration_max_minutes for addon in addons),
            prepayment_amount=terms.prepayment_amount,
            prepayment_percent=terms.prepayment_percent,
            online_booking_enabled=terms.online_booking_enabled,
        )

    @staticmethod
    async def _selected_addons(
        unit_of_work: SqlAlchemyUnitOfWork,
        service_id: int,
        addon_ids: Sequence[int],
        *,
        for_update: bool,
    ) -> list[ServiceAddon]:
        requested = list(addon_ids)
        if len(requested) != len(set(requested)) or len(requested) > 20:
            raise BookingUnavailableError("Выбран некорректный набор дополнительных услуг.")
        if not requested:
            return []
        if for_update:
            rows = await unit_of_work.service_addons.get_selected_for_update(service_id, requested)
        else:
            active = await unit_of_work.service_addons.list_active(service_id)
            requested_set = set(requested)
            rows = [row for row in active if row.id in requested_set]
        if {row.id for row in rows} != set(requested):
            raise BookingUnavailableError(
                "Одна из дополнительных услуг больше недоступна. Выберите дополнения заново."
            )
        by_id = {row.id: row for row in rows}
        return [by_id[addon_id] for addon_id in requested]

    def _required_payment_service(self, mode: PaymentMode) -> PaymentService:
        service = self._payment_services.get(mode)
        if service is None:
            raise BookingUnavailableError("Платёжный режим ещё не настроен владельцем бизнеса.")
        return service

    async def book_for_client(
        self,
        actor: AdminActor,
        *,
        client_id: int,
        service_id: int,
        window_id: int,
        staff_context: StaffContext | None = None,
        quota_override_reason: str | None = None,
        quota_override_confirmed: bool = False,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> BookingReceipt:
        """Create a manual booking while preserving every transactional booking invariant."""

        ensure_admin(actor, self._admin_telegram_ids)
        override: _BookingLimitOverride | None = None
        if quota_override_reason is not None:
            if not quota_override_confirmed:
                raise AuthorizationError("Требуется явное подтверждение превышения лимита.")
            if self._authorization_service is None or staff_context is None:
                raise AuthorizationError("Недостаточно прав для превышения лимита.")
            live_actor = await self._authorization_service.authorize(
                business_id=staff_context.business_id,
                telegram_id=staff_context.telegram_id,
                permission=StaffPermission.OVERRIDE_BOOKING_LIMIT,
            )
            if live_actor.telegram_id != actor.telegram_id:
                raise AuthorizationError("Недостаточно прав для превышения лимита.")
            normalized_reason = quota_override_reason.strip()
            if normalized_reason == "-":
                normalized_reason = "repeat_session"
            if not 1 <= len(normalized_reason) <= 500:
                raise BookingUnavailableError(
                    "Причина превышения лимита должна содержать от 1 до 500 символов."
                )
            override = _BookingLimitOverride(live_actor.user_id, normalized_reason)
        async with self._unit_of_work_factory() as unit_of_work:
            client = await unit_of_work.users.get_by_id(client_id)
            if client is None:
                raise BookingUnavailableError("Клиент больше не существует.")
            if not client.first_name or not client.phone:
                raise BookingUnavailableError(
                    "В карточке клиента должны быть заполнены имя и телефон."
                )
            client_actor = ClientActor(
                telegram_id=client.telegram_id,
                username=client.username,
                first_name=client.first_name,
                last_name=client.last_name,
            )
            request = BookingRequest(
                service_id=service_id,
                window_id=window_id,
                client_name=client.first_name,
                phone=client.phone,
            )
        return await self.book(
            client_actor,
            request,
            now=now,
            correlation_id=correlation_id,
            allow_self_booking_blocked=True,
            _booking_limit_override=override,
        )

    @staticmethod
    async def _enforce_future_booking_limit(
        unit_of_work: SqlAlchemyUnitOfWork,
        settings: BusinessSettings,
        client_id: int,
        now: datetime,
        *,
        override: _BookingLimitOverride | None,
        correlation_id: str | None,
    ) -> None:
        enabled_value = getattr(settings, "future_booking_limit_enabled", None)
        enabled = True if enabled_value is None else bool(enabled_value)
        if not enabled:
            if override is not None:
                raise BookingUnavailableError("Превышение лимита больше не требуется.")
            return
        maximum = int(getattr(settings, "future_booking_limit_max", None) or 4)
        horizon_days = int(getattr(settings, "future_booking_limit_horizon_days", None) or 30)
        include_cancellations = bool(
            getattr(settings, "future_booking_count_client_cancellations", False)
        )
        business_client = await unit_of_work.reservations.lock_client_for_booking(client_id)
        if business_client is None:
            raise BookingUnavailableError("Карточка клиента неактивна.")
        current = await unit_of_work.appointments.count_for_future_booking_limit(
            client_id=client_id,
            now=now,
            horizon_days=horizon_days,
            include_client_cancellations=include_cancellations,
        )
        if current < maximum:
            if override is not None:
                raise BookingUnavailableError("Превышение лимита больше не требуется.")
            return
        if override is None:
            raise FutureBookingLimitError(
                "Достигнут лимит будущих записей на ближайшие 30 дней.",
                current=current,
                maximum=maximum,
            )
        await unit_of_work.audit.add(
            actor_user_id=override.actor_user_id,
            action="appointment.booking_limit_overridden",
            entity_type="user",
            entity_id=str(client_id),
            changes={
                "current": current,
                "maximum": maximum,
                "horizon_days": horizon_days,
                "include_client_cancellations": include_cancellations,
                "reason": override.reason,
            },
            correlation_id=correlation_id,
        )

    @staticmethod
    async def _consented_client(
        unit_of_work: SqlAlchemyUnitOfWork,
        telegram_id: int,
        *,
        for_update: bool = False,
        allow_self_booking_blocked: bool = False,
    ) -> User:
        user = await unit_of_work.users.get_by_telegram_id(
            telegram_id,
            for_update=for_update,
        )
        if user is None or user.privacy_consent_at is None:
            raise PrivacyConsentRequiredError(
                "Сначала примите условия обработки данных через команду /start."
            )
        if user.is_self_booking_blocked and not allow_self_booking_blocked:
            raise BookingUnavailableError(
                "Самостоятельная запись временно недоступна. Напишите мастеру."
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

    @staticmethod
    def _service_fits(service: Service, start_at: datetime, end_at: datetime) -> bool:
        return service.duration_max_minutes * 60 <= (end_at - start_at).total_seconds()

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
