"""Appointment ownership, cancellation and confirmation transaction tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import (
    Appointment,
    AppointmentReferenceMedia,
    AvailabilityWindow,
    BusinessSettings,
    User,
)
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    MediaType,
    NotificationType,
)
from app.domain.errors import AppointmentNotFoundError, CancellationDeadlineError
from app.schemas.booking import ClientActor, ReferenceMediaDraft
from app.schemas.service import AdminActor
from app.services.appointment_service import AppointmentService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


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
        portfolio_page_size=5,
        portfolio_max_media=8,
        waitlist_default_expiration_days=31,
        waitlist_notification_cooldown_minutes=180,
        review_request_delay_minutes=60,
        repeat_booking_reminder_days=28,
        broadcast_messages_per_second=15,
        broadcast_max_media=5,
        broadcast_max_retries=5,
        broadcast_retry_base_seconds=15,
        client_page_size=10,
        reviews_enabled=True,
        waitlist_enabled=True,
        broadcasts_enabled=False,
        portfolio_enabled=True,
        availability_date_picker_days=31,
        availability_time_step_minutes=60,
        booking_reference_max_media=10,
        booking_reference_edit_deadline_hours=36,
        booking_reference_retention_days=None,
        portfolio_mode="internal",
        external_portfolio_url=None,
        external_portfolio_button_text="Открыть портфолио",
        master_profile_enabled=True,
        version=1,
    )


def client(user_id: int = 5, telegram_id: int = 101) -> User:
    return User(id=user_id, telegram_id=telegram_id, first_name="Анна", phone="+79991234567")


def appointment(*, client_id: int = 5) -> Appointment:
    return Appointment(
        id=11,
        client_id=client_id,
        window_id=7,
        service_id=3,
        service_name_snapshot="Маникюр",
        price_snapshot=Decimal("2500.00"),
        duration_min_snapshot=120,
        duration_max_snapshot=180,
        status=AppointmentStatus.CONFIRMED,
    )


def window(*, hours_until: int = 36) -> AvailabilityWindow:
    start_at = NOW + timedelta(hours=hours_until)
    return AvailabilityWindow(
        id=7,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=210),
        status=AvailabilityWindowStatus.BOOKED,
        created_by=9,
    )


def build_uow(
    *,
    target_appointment: Appointment | None = None,
    target_window: AvailabilityWindow | None = None,
    target_client: User | None = None,
) -> MagicMock:
    target_appointment = target_appointment or appointment()
    target_window = target_window or window()
    target_client = target_client or client()
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    unit_of_work.appointments.get = AsyncMock(return_value=target_appointment)
    unit_of_work.appointments.list_for_client = AsyncMock(return_value=[])
    unit_of_work.appointments.add_history = AsyncMock()
    unit_of_work.windows.get = AsyncMock(return_value=target_window)
    unit_of_work.windows.get_many_for_update = AsyncMock(return_value=[target_window])
    unit_of_work.windows.lock_local_date = AsyncMock()
    unit_of_work.users.get_by_telegram_id = AsyncMock(return_value=target_client)
    unit_of_work.users.get_by_id = AsyncMock(return_value=target_client)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=9))
    unit_of_work.notifications.cancel_unsent = AsyncMock(return_value=2)
    unit_of_work.notifications.add_all = AsyncMock()
    unit_of_work.reference_media.list_active = AsyncMock(return_value=[])
    unit_of_work.reference_media.add = AsyncMock(
        side_effect=lambda row: setattr(row, "id", 8) or row
    )
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.waitlist.list_matching = AsyncMock(return_value=[])
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_client_cannot_view_another_clients_appointment() -> None:
    unit_of_work = build_uow(target_appointment=appointment(client_id=99))
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(AppointmentNotFoundError, match="не найдена"):
        await service.get_my(ClientActor(telegram_id=101), 11, now=NOW)


@pytest.mark.asyncio
async def test_reference_media_requires_owner_but_is_visible_to_admin() -> None:
    foreign_appointment = appointment(client_id=99)
    unit_of_work = build_uow(target_appointment=foreign_appointment)
    unit_of_work.reference_media.list_active.return_value = [
        AppointmentReferenceMedia(
            id=4,
            appointment_id=11,
            telegram_file_id="file-1",
            telegram_file_unique_id="unique-1",
            media_type=MediaType.PHOTO,
            position=0,
            uploaded_by_user_id=99,
        )
    ]
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(AppointmentNotFoundError):
        await service.list_my_reference_media(ClientActor(telegram_id=101), 11)
    media = await service.list_admin_reference_media(AdminActor(telegram_id=900), 11)

    assert media[0].telegram_file_unique_id == "unique-1"
    unit_of_work.reference_media.list_active.assert_awaited_once_with(11)


@pytest.mark.asyncio
async def test_client_can_add_reference_only_before_configured_deadline() -> None:
    unit_of_work = build_uow(target_window=window(hours_until=37))
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]
    values = ReferenceMediaDraft(
        telegram_file_id="file-2",
        telegram_file_unique_id="unique-2",
    )

    added = await service.add_my_reference_media(
        ClientActor(telegram_id=101),
        11,
        values,
        now=NOW,
        correlation_id="request-ref",
    )

    assert added.position == 0
    assert added.telegram_file_unique_id == "unique-2"
    assert unit_of_work.audit.add.await_args.kwargs["action"] == "booking_reference.added"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reference_removal_inside_deadline_is_rejected_without_mutation() -> None:
    row = AppointmentReferenceMedia(
        id=4,
        appointment_id=11,
        telegram_file_id="file-1",
        telegram_file_unique_id="unique-1",
        media_type=MediaType.PHOTO,
        position=0,
        uploaded_by_user_id=5,
    )
    unit_of_work = build_uow(target_window=window(hours_until=35))
    unit_of_work.reference_media.list_active.return_value = [row]
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(CancellationDeadlineError, match="истёк"):
        await service.clear_my_reference_media(ClientActor(telegram_id=101), 11, now=NOW)

    assert row.deleted_at is None
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_cancellation_at_exact_deadline_reopens_window() -> None:
    target_appointment = appointment()
    target_window = window(hours_until=36)
    unit_of_work = build_uow(
        target_appointment=target_appointment,
        target_window=target_window,
    )
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    cancelled = await service.cancel_my(
        ClientActor(telegram_id=101),
        11,
        now=NOW,
        correlation_id="request-1",
    )

    assert cancelled.status is AppointmentStatus.CANCELLED_BY_CLIENT
    assert target_window.status is AvailabilityWindowStatus.OPEN
    unit_of_work.notifications.cancel_unsent.assert_awaited_once_with(11)
    history = unit_of_work.appointments.add_history.await_args.args[0]
    assert history.new_status is AppointmentStatus.CANCELLED_BY_CLIENT
    assert unit_of_work.audit.add.await_args.kwargs["correlation_id"] == "request-1"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_cancellation_inside_deadline_does_not_mutate() -> None:
    target_appointment = appointment()
    target_window = window(hours_until=35)
    unit_of_work = build_uow(
        target_appointment=target_appointment,
        target_window=target_window,
    )
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(CancellationDeadlineError):
        await service.cancel_my(ClientActor(telegram_id=101), 11, now=NOW)

    assert target_appointment.status is AppointmentStatus.CONFIRMED
    assert target_window.status is AvailabilityWindowStatus.BOOKED
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_can_cancel_inside_deadline_and_keep_window_closed() -> None:
    target_appointment = appointment()
    target_window = window(hours_until=1)
    unit_of_work = build_uow(
        target_appointment=target_appointment,
        target_window=target_window,
    )
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    cancelled = await service.cancel_admin(
        AdminActor(telegram_id=900),
        11,
        reopen_window=False,
        now=NOW,
    )

    assert cancelled.status is AppointmentStatus.CANCELLED_BY_ADMIN
    assert target_window.status is AvailabilityWindowStatus.CLOSED
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_manual_confirmation_writes_history() -> None:
    target_appointment = appointment()
    unit_of_work = build_uow(target_appointment=target_appointment)
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    confirmed = await service.confirm_visit(AdminActor(telegram_id=900), 11, now=NOW)

    assert confirmed.status is AppointmentStatus.CLIENT_CONFIRMED
    assert target_appointment.client_confirmed_at == NOW
    unit_of_work.appointments.add_history.assert_awaited_once()
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_can_confirm_only_own_visit_from_reminder() -> None:
    target_appointment = appointment()
    unit_of_work = build_uow(target_appointment=target_appointment)
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    confirmed = await service.confirm_my_visit(
        ClientActor(telegram_id=101),
        11,
        now=NOW,
        correlation_id="request-reminder",
    )

    assert confirmed.status is AppointmentStatus.CLIENT_CONFIRMED
    assert target_appointment.client_confirmed_at == NOW
    assert unit_of_work.audit.add.await_args.kwargs["correlation_id"] == "request-reminder"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_visit_schedules_exactly_one_review_request() -> None:
    target_appointment = appointment()
    target_window = window(hours_until=-4)
    unit_of_work = build_uow(
        target_appointment=target_appointment,
        target_window=target_window,
    )
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    completed = await service.complete_visit(
        AdminActor(telegram_id=900), 11, now=NOW, correlation_id="complete-1"
    )

    assert completed.status is AppointmentStatus.COMPLETED
    assert target_appointment.completed_at == NOW
    assert target_window.status is AvailabilityWindowStatus.CLOSED
    jobs = unit_of_work.notifications.add_all.await_args.args[0]
    assert len(jobs) == 1
    assert jobs[0].notification_type is NotificationType.REVIEW_REQUEST
    assert jobs[0].available_at == NOW + timedelta(minutes=60)

    await service.complete_visit(AdminActor(telegram_id=900), 11, now=NOW)
    assert unit_of_work.notifications.add_all.await_count == 1


@pytest.mark.asyncio
async def test_completed_visit_schedules_repeat_only_with_marketing_consent() -> None:
    target_client = client()
    target_client.marketing_consent_at = NOW
    target_appointment = appointment()
    unit_of_work = build_uow(
        target_appointment=target_appointment,
        target_window=window(hours_until=-4),
        target_client=target_client,
    )
    service = AppointmentService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    await service.complete_visit(AdminActor(telegram_id=900), 11, now=NOW)

    batches = [call.args[0] for call in unit_of_work.notifications.add_all.await_args_list]
    jobs = [job for batch in batches for job in batch]
    assert [job.notification_type for job in jobs] == [
        NotificationType.REVIEW_REQUEST,
        NotificationType.REPEAT_BOOKING_REMINDER,
    ]
    assert jobs[1].available_at == NOW + timedelta(days=28)
