"""Transactional booking revalidation and snapshot tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import (
    AvailabilityWindow,
    BusinessFeatureFlags,
    BusinessSettings,
    PortfolioItem,
    Service,
    StaffMember,
    StaffServiceAssignment,
    User,
)
from app.database.models.commerce import BusinessPaymentSettings
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    ManualPaymentStatus,
    PaymentMode,
    PaymentStatus,
    PortfolioStatus,
    StaffRole,
)
from app.domain.errors import (
    BookingConflictError,
    BookingLimitError,
    BookingUnavailableError,
    PrivacyConsentRequiredError,
)
from app.payments.providers.mock import MockPaymentProvider
from app.schemas.booking import BookingRequest, ClientActor, ReferenceMediaDraft
from app.schemas.service import AdminActor
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService
from app.services.subscription_service import SubscriptionAccessError, SubscriptionService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def actor() -> ClientActor:
    return ClientActor(telegram_id=101, username="client", first_name="Telegram name")


def settings() -> BusinessSettings:
    return BusinessSettings(
        id=1,
        business_name="lanrouge nails",
        timezone="Europe/Moscow",
        address="Новоостаповская, д. 20",
        map_url="https://yandex.ru/maps/-/CTbJz23i",
        master_telegram_url="https://t.me/lanrouge",
        booking_horizon_days=31,
        cancellation_deadline_hours=36,
        max_appointments_per_day=2,
        default_window_duration_minutes=210,
        minimum_gap_minutes=60,
        allow_saturday=False,
        allow_sunday=False,
        reminder_offsets_minutes=[1440, 180, 60],
        booking_reference_max_media=10,
        booking_reference_edit_deadline_hours=36,
        version=1,
    )


def client(*, consented: bool = True) -> User:
    return User(
        id=5,
        telegram_id=101,
        first_name="Telegram name",
        privacy_consent_at=NOW if consented else None,
        is_blocked=False,
    )


def catalog_service(*, duration_max: int = 180, active: bool = True) -> Service:
    return Service(
        id=3,
        business_id=1,
        name="Маникюр с покрытием",
        description=None,
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=duration_max,
        is_active=active,
    )


def window(
    status: AvailabilityWindowStatus = AvailabilityWindowStatus.OPEN,
    *,
    window_id: int = 7,
    staff_member_id: int = 1,
) -> AvailabilityWindow:
    return AvailabilityWindow(
        id=window_id,
        business_id=1,
        staff_member_id=staff_member_id,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        status=status,
        admin_comment="never expose",
        created_by=9,
    )


def request() -> BookingRequest:
    return BookingRequest(
        service_id=3,
        window_id=7,
        client_name="Анна",
        phone="+7 999 123-45-67",
        client_comment="Первый визит",
    )


def build_uow(
    *,
    target_window: AvailabilityWindow | None = None,
    target_service: Service | None = None,
    target_client: User | None = None,
) -> MagicMock:
    target_window = target_window or window()
    target_service = target_service or catalog_service()
    target_client = target_client or client()
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.reservations.business_id = 1
    unit_of_work.payments.business_id = 1
    unit_of_work.audit.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    unit_of_work.features.get = AsyncMock(
        return_value=BusinessFeatureFlags(
            business_id=1,
            online_booking=True,
            master_selection=True,
            reference_photos=True,
        )
    )
    unit_of_work.windows.get = AsyncMock(return_value=target_window)
    unit_of_work.windows.lock_local_date = AsyncMock()
    unit_of_work.users.get_by_telegram_id = AsyncMock(return_value=target_client)
    unit_of_work.users.get_by_id = AsyncMock(return_value=target_client)
    unit_of_work.users.update_booking_profile = AsyncMock()
    unit_of_work.users.list_by_telegram_ids = AsyncMock(
        return_value=[SimpleNamespace(id=9, is_blocked=False)]
    )
    unit_of_work.staff.list_active_by_roles = AsyncMock(
        return_value=[(SimpleNamespace(), SimpleNamespace(id=9, is_blocked=False))]
    )
    target_master = StaffMember(
        id=1,
        business_id=1,
        display_name="Основной мастер",
        role=StaffRole.MASTER,
        is_active=True,
        is_bookable=True,
    )
    unit_of_work.staff.get_by_id = AsyncMock(return_value=target_master)
    target_assignment = StaffServiceAssignment(
        id=1,
        business_id=1,
        staff_member_id=1,
        service_id=3,
        online_booking_enabled=True,
        is_active=True,
    )
    unit_of_work.service_assignments.get_assignment = AsyncMock(return_value=target_assignment)
    unit_of_work.service_assignments.list_bookable_assignments = AsyncMock(
        return_value=[(target_assignment, target_service, target_master)]
    )
    unit_of_work.services.get = AsyncMock(return_value=target_service)
    unit_of_work.services.list_active = AsyncMock(return_value=[target_service])
    unit_of_work.portfolio.get = AsyncMock(return_value=None)
    unit_of_work.appointments.count_capacity_between = AsyncMock(return_value=0)
    unit_of_work.windows.list_open_between = AsyncMock(return_value=[target_window])
    unit_of_work.reservations.get_by_idempotency_key = AsyncMock(return_value=None)
    unit_of_work.reservations.payment_settings = AsyncMock(
        return_value=SimpleNamespace(
            mode=PaymentMode.DISABLED,
            reservation_ttl_minutes=20,
            provider_account_ref=None,
            manual_payment_instructions=None,
        )
    )

    async def add_appointment(appointment: object) -> object:
        appointment.id = 11  # type: ignore[attr-defined]
        return appointment

    unit_of_work.appointments.add = AsyncMock(side_effect=add_appointment)
    unit_of_work.appointments.get = AsyncMock(return_value=None)
    unit_of_work.appointments.add_history = AsyncMock()
    unit_of_work.notifications.add_all = AsyncMock()
    unit_of_work.reference_media.add_all = AsyncMock(
        side_effect=lambda rows: [setattr(row, "id", index + 1) for index, row in enumerate(rows)]
    )
    unit_of_work.waitlist.mark_booked_for_window = AsyncMock(return_value=[])
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


def configure_payment_checkout(
    unit_of_work: MagicMock,
    *,
    mode: PaymentMode,
) -> dict[str, object]:
    captured: dict[str, object] = {}
    service = unit_of_work.services.get.return_value
    service.prepayment_amount = Decimal("500.00")
    flags = unit_of_work.features.get.return_value
    flags.prepayment = True
    flags.manual_payments = mode is PaymentMode.MANUAL
    flags.yookassa_payments = mode is PaymentMode.YOOKASSA
    unit_of_work.reservations.payment_settings = AsyncMock(
        return_value=BusinessPaymentSettings(
            business_id=1,
            mode=mode,
            provider_account_ref="configured-account",
            manual_payment_instructions="Переведите по безопасной ссылке мастера.",
            reservation_ttl_minutes=20,
        )
    )

    async def add_appointment(value: object) -> object:
        value.id = 11  # type: ignore[attr-defined]
        captured["appointment"] = value
        return value

    async def add_reservation(value: object) -> object:
        value.id = 21  # type: ignore[attr-defined]
        captured["reservation"] = value
        return value

    async def add_payment(value: object) -> object:
        value.id = 31  # type: ignore[attr-defined]
        captured["payment"] = value
        return value

    unit_of_work.appointments.add = AsyncMock(side_effect=add_appointment)
    unit_of_work.appointments.get = AsyncMock(
        side_effect=lambda _appointment_id: captured.get("appointment")
    )
    unit_of_work.reservations.get_window_for_update = AsyncMock(
        return_value=unit_of_work.windows.get.return_value
    )
    unit_of_work.reservations.get_active_for_window = AsyncMock(return_value=None)
    unit_of_work.reservations.get_appointment_for_update = AsyncMock(
        side_effect=lambda _: captured.get("appointment")
    )
    unit_of_work.reservations.add = AsyncMock(side_effect=add_reservation)
    unit_of_work.payments.add = AsyncMock(side_effect=add_payment)
    unit_of_work.payments.get = AsyncMock(
        side_effect=lambda *_args, **_kwargs: captured.get("payment")
    )
    unit_of_work.payments.get_latest_for_appointment = AsyncMock(
        side_effect=lambda *_args, **_kwargs: captured.get("payment")
    )
    unit_of_work.reference_media.list_active = AsyncMock(return_value=[])
    return captured


@pytest.mark.asyncio
async def test_privacy_consent_is_required_to_view_catalog() -> None:
    unit_of_work = build_uow(target_client=client(consented=False))
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(PrivacyConsentRequiredError):
        await booking.list_active_services(actor())


@pytest.mark.asyncio
async def test_catalog_access_uses_persisted_consent_without_an_env_policy_flag() -> None:
    unit_of_work = build_uow(target_client=client(consented=True))
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    services = await booking.list_active_services(actor())

    assert [item.id for item in services] == [3]


@pytest.mark.asyncio
async def test_manual_payment_creates_pending_reservation_and_safe_instructions() -> None:
    unit_of_work = build_uow()
    captured = configure_payment_checkout(unit_of_work, mode=PaymentMode.MANUAL)
    booking = BookingService(lambda: unit_of_work, frozenset())  # type: ignore[arg-type]
    values = request()

    receipt = await booking.book(actor(), values, now=NOW, correlation_id="manual-booking")

    appointment = captured["appointment"]
    payment = captured["payment"]
    assert appointment.status is AppointmentStatus.PENDING_PAYMENT  # type: ignore[attr-defined]
    assert payment.status is PaymentStatus.PENDING  # type: ignore[attr-defined]
    assert payment.manual_status is ManualPaymentStatus.AWAITING_PAYMENT  # type: ignore[attr-defined]
    assert receipt.appointment_status is AppointmentStatus.PENDING_PAYMENT
    assert receipt.payment_mode is PaymentMode.MANUAL
    assert receipt.payment_status is PaymentStatus.PENDING
    assert receipt.payment_id == 31
    assert receipt.manual_payment_instructions is not None
    assert receipt.reservation_expires_at is not None
    assert unit_of_work.windows.get.return_value.status is AvailabilityWindowStatus.RESERVED


@pytest.mark.asyncio
async def test_yookassa_checkout_is_provider_idempotent_and_returns_only_safe_url() -> None:
    unit_of_work = build_uow()
    captured = configure_payment_checkout(unit_of_work, mode=PaymentMode.YOOKASSA)
    provider = MockPaymentProvider()
    booking = BookingService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        frozenset(),
        payment_services={PaymentMode.YOOKASSA: PaymentService(provider)},
        payment_return_url="https://t.me/example_bot",
    )
    values = request()

    first = await booking.book(actor(), values, now=NOW, correlation_id="yoo-booking")
    reservation = captured["reservation"]
    unit_of_work.reservations.get_by_idempotency_key = AsyncMock(return_value=reservation)
    second = await booking.book(actor(), values, now=NOW, correlation_id="retry")

    assert first.appointment_status is AppointmentStatus.PENDING_PAYMENT
    assert first.payment_status is PaymentStatus.PENDING
    assert first.payment_confirmation_url is not None
    assert first.payment_confirmation_url.startswith("https://payments.example.test/")
    assert second.payment_id == first.payment_id
    assert second.payment_confirmation_url == first.payment_confirmation_url
    assert unit_of_work.appointments.add.await_count == 1


@pytest.mark.asyncio
async def test_bookable_master_options_follow_service_assignments_and_feature_flag() -> None:
    unit_of_work = build_uow()
    second_master = StaffMember(
        id=2,
        business_id=1,
        display_name="Второй мастер",
        specialization="Дизайн",
        role=StaffRole.MASTER,
        is_active=True,
        is_bookable=True,
    )
    second_assignment = StaffServiceAssignment(
        id=2,
        business_id=1,
        staff_member_id=2,
        service_id=3,
        online_booking_enabled=True,
        is_active=True,
    )
    first_row = unit_of_work.service_assignments.list_bookable_assignments.return_value[0]
    unit_of_work.service_assignments.list_bookable_assignments = AsyncMock(
        return_value=[first_row, (second_assignment, catalog_service(), second_master)]
    )
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    options = await booking.list_bookable_masters(actor(), 3)

    assert options.selection_enabled
    assert [(master.id, master.display_name) for master in options.masters] == [
        (1, "Основной мастер"),
        (2, "Второй мастер"),
    ]
    unit_of_work.service_assignments.list_bookable_assignments.assert_awaited_once_with(1, 3)


@pytest.mark.asyncio
async def test_availability_is_scoped_to_selected_assigned_master() -> None:
    unit_of_work = build_uow()
    second_master = StaffMember(
        id=2,
        business_id=1,
        display_name="Второй мастер",
        role=StaffRole.MASTER,
        is_active=True,
        is_bookable=True,
    )
    second_assignment = StaffServiceAssignment(
        id=2,
        business_id=1,
        staff_member_id=2,
        service_id=3,
        online_booking_enabled=True,
        is_active=True,
    )
    first_row = unit_of_work.service_assignments.list_bookable_assignments.return_value[0]
    unit_of_work.service_assignments.list_bookable_assignments = AsyncMock(
        return_value=[first_row, (second_assignment, catalog_service(), second_master)]
    )
    unit_of_work.windows.list_open_between = AsyncMock(
        return_value=[window(), window(window_id=8, staff_member_id=2)]
    )
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    availability = await booking.list_availability(
        actor(),
        3,
        staff_member_id=2,
        now=NOW,
    )

    assert [(item.id, item.staff_member_id, item.master_name) for item in availability.windows] == [
        (8, 2, "Второй мастер")
    ]
    unit_of_work.appointments.count_capacity_between.assert_awaited_once()


@pytest.mark.asyncio
async def test_master_pause_hides_only_windows_before_pause_deadline() -> None:
    unit_of_work = build_uow()
    master = unit_of_work.staff.get_by_id.return_value
    master.schedule_paused_until = datetime(2026, 7, 24, 7, tzinfo=UTC)
    unit_of_work.windows.list_open_between = AsyncMock(
        return_value=[
            window(window_id=7),
            AvailabilityWindow(
                id=8,
                business_id=1,
                staff_member_id=1,
                start_at=datetime(2026, 7, 24, 7, tzinfo=UTC),
                end_at=datetime(2026, 7, 24, 10, 30, tzinfo=UTC),
                status=AvailabilityWindowStatus.OPEN,
                created_by=9,
            ),
        ]
    )
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    availability = await booking.list_availability(actor(), 3, now=NOW)

    assert [item.id for item in availability.windows] == [8]


@pytest.mark.asyncio
async def test_master_pause_is_revalidated_under_booking_lock() -> None:
    unit_of_work = build_uow()
    unit_of_work.staff.get_by_id.return_value.schedule_paused_until = datetime(
        2026,
        7,
        24,
        7,
        tzinfo=UTC,
    )
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(BookingUnavailableError, match="temporarily unavailable"):
        await booking.book(actor(), request(), now=NOW)

    unit_of_work.appointments.add.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_or_already_booked_window_cannot_be_booked() -> None:
    unit_of_work = build_uow(
        target_window=window(AvailabilityWindowStatus.CLOSED),
    )
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(BookingConflictError, match="только что заняли"):
        await booking.book(actor(), request(), now=NOW)

    unit_of_work.appointments.add.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_maximum_duration_must_fit_window() -> None:
    unit_of_work = build_uow(target_service=catalog_service(duration_max=211))
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(BookingUnavailableError, match="не помещается"):
        await booking.book(actor(), request(), now=NOW)

    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_capacity_is_rechecked_under_date_lock() -> None:
    unit_of_work = build_uow()
    unit_of_work.appointments.count_capacity_between = AsyncMock(return_value=2)
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(BookingLimitError, match="нет мест"):
        await booking.book(actor(), request(), now=NOW)

    unit_of_work.windows.lock_local_date.assert_awaited_once_with(
        date(2026, 7, 23), staff_member_id=1
    )
    unit_of_work.appointments.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_booking_snapshots_service_and_schedules_only_future_jobs() -> None:
    service = catalog_service()
    target_window = window()
    unit_of_work = build_uow(target_window=target_window, target_service=service)
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    receipt = await booking.book(actor(), request(), now=NOW, correlation_id="request-1")

    appointment = unit_of_work.appointments.add.await_args.args[0]
    assert receipt.appointment_id == 11
    assert receipt.master_name == "Основной мастер"
    assert appointment.service_name_snapshot == "Маникюр с покрытием"
    assert appointment.price_snapshot == Decimal("2500.00")
    assert target_window.status is AvailabilityWindowStatus.BOOKED
    jobs = unit_of_work.notifications.add_all.await_args.args[0]
    assert len(jobs) == 4
    assert {job.offset_minutes for job in jobs} == {180, 60}
    audit = unit_of_work.audit.add.await_args.kwargs
    assert audit["correlation_id"] == "request-1"
    assert "Первый визит" not in str(audit["changes"])
    assert "+79991234567" not in str(audit["changes"])
    unit_of_work.commit.assert_awaited_once()

    service.price = Decimal("3000.00")
    assert appointment.price_snapshot == Decimal("2500.00")


@pytest.mark.asyncio
async def test_selected_design_is_snapshotted_on_booking() -> None:
    unit_of_work = build_uow()
    design = PortfolioItem(
        id=21,
        title="Красный френч",
        linked_service_id=3,
        status=PortfolioStatus.PUBLISHED,
        sort_order=0,
        created_by=9,
    )
    unit_of_work.portfolio.get = AsyncMock(return_value=design)
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]
    design_request = request().model_copy(update={"design_reference_id": 21})

    receipt = await booking.book(actor(), design_request, now=NOW)

    appointment = unit_of_work.appointments.add.await_args.args[0]
    assert appointment.design_reference_id == 21
    assert appointment.design_title_snapshot == "Красный френч"
    assert receipt.design_title == "Красный френч"


@pytest.mark.asyncio
async def test_reference_media_is_created_atomically_after_appointment() -> None:
    unit_of_work = build_uow()
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]
    values = request().model_copy(
        update={
            "reference_media": [
                ReferenceMediaDraft(
                    telegram_file_id="telegram-file-1",
                    telegram_file_unique_id="unique-1",
                ),
                ReferenceMediaDraft(
                    telegram_file_id="telegram-file-2",
                    telegram_file_unique_id="unique-2",
                ),
            ]
        }
    )

    receipt = await booking.book(actor(), values, now=NOW)

    rows = unit_of_work.reference_media.add_all.await_args.args[0]
    assert [row.appointment_id for row in rows] == [11, 11]
    assert [row.position for row in rows] == [0, 1]
    assert [row.uploaded_by_user_id for row in rows] == [5, 5]
    assert [row.expires_at for row in rows] == [
        window().end_at + timedelta(days=30),
        window().end_at + timedelta(days=30),
    ]
    assert [item.telegram_file_unique_id for item in receipt.reference_media] == [
        "unique-1",
        "unique-2",
    ]
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_or_excess_reference_media_is_rejected_before_insert() -> None:
    unit_of_work = build_uow()
    unit_of_work.settings.get.return_value.booking_reference_max_media = 1
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]
    media = ReferenceMediaDraft(
        telegram_file_id="telegram-file-1",
        telegram_file_unique_id="unique-1",
    )

    with pytest.raises(BookingUnavailableError, match="Превышено"):
        await booking.book(
            actor(),
            request().model_copy(update={"reference_media": [media, media]}),
            now=NOW,
        )

    unit_of_work.reference_media.add_all.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_manual_booking_bypasses_only_self_booking_block() -> None:
    blocked_client = client()
    blocked_client.phone = "+79991234567"
    blocked_client.is_self_booking_blocked = True
    unit_of_work = build_uow(target_client=blocked_client)
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    receipt = await booking.book_for_client(
        AdminActor(telegram_id=900),
        client_id=5,
        service_id=3,
        window_id=7,
        now=NOW,
    )

    assert receipt.appointment_id == 11
    unit_of_work.appointments.add.assert_awaited_once()


@pytest.mark.asyncio
async def test_two_sequential_attempts_on_same_state_have_one_winner() -> None:
    target_window = window()
    unit_of_work = build_uow(target_window=target_window)
    booking = BookingService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    await booking.book(actor(), request(), now=NOW)
    with pytest.raises(BookingConflictError):
        await booking.book(actor(), request(), now=NOW)

    assert unit_of_work.appointments.add.await_count == 1
    assert unit_of_work.commit.await_count == 1


@pytest.mark.asyncio
async def test_expired_crm_subscription_blocks_a_new_checkout_before_slot_mutation() -> None:
    unit_of_work = build_uow()
    subscription = MagicMock(spec=SubscriptionService)
    subscription.ensure_new_bookings_allowed = AsyncMock(
        side_effect=SubscriptionAccessError("subscription_expired")
    )
    booking = BookingService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        frozenset({900}),
        subscription_service=subscription,
    )

    with pytest.raises(SubscriptionAccessError):
        await booking.book(actor(), request(), now=NOW)

    subscription.ensure_new_bookings_allowed.assert_awaited_once_with(1, now=NOW)
    unit_of_work.appointments.add.assert_not_awaited()
