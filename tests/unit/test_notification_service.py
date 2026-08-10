"""Persistent notification lease, revalidation and finalization tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import (
    Appointment,
    AvailabilityWindow,
    BusinessSettings,
    NotificationJob,
    User,
)
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    NotificationJobStatus,
    NotificationType,
)
from app.services.notification_service import NotificationService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def job(*, attempts: int = 1, worker_id: str = "worker-1") -> NotificationJob:
    return NotificationJob(
        id=21,
        appointment_id=11,
        recipient_user_id=5,
        notification_type=NotificationType.CLIENT_REMINDER,
        offset_minutes=1440,
        scheduled_at=NOW,
        available_at=NOW,
        status=NotificationJobStatus.PROCESSING,
        attempts=attempts,
        locked_at=NOW,
        locked_by=worker_id,
    )


def appointment(
    status: AppointmentStatus = AppointmentStatus.CONFIRMED,
) -> Appointment:
    return Appointment(
        id=11,
        client_id=5,
        window_id=7,
        service_id=3,
        service_name_snapshot="Маникюр",
        price_snapshot=Decimal("2500.00"),
        duration_min_snapshot=120,
        duration_max_snapshot=180,
        status=status,
    )


def window() -> AvailabilityWindow:
    return AvailabilityWindow(
        id=7,
        start_at=NOW + timedelta(days=1),
        end_at=NOW + timedelta(days=1, hours=3),
        status=AvailabilityWindowStatus.BOOKED,
        created_by=9,
    )


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
        reviews_enabled=True,
        version=1,
    )


def build_uow(
    *,
    target_job: NotificationJob | None = None,
    appointment_status: AppointmentStatus = AppointmentStatus.CONFIRMED,
    recipient_blocked: bool = False,
) -> tuple[MagicMock, NotificationJob, User]:
    target_job = target_job or job()
    recipient = User(
        id=5,
        telegram_id=101,
        first_name="Анна",
        phone="+79991234567",
        is_blocked=recipient_blocked,
    )
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.notifications.get = AsyncMock(return_value=target_job)
    unit_of_work.notifications.claim_due = AsyncMock(return_value=[target_job])
    unit_of_work.appointments.get = AsyncMock(return_value=appointment(status=appointment_status))
    unit_of_work.appointments.has_future_active_for_client = AsyncMock(return_value=False)
    unit_of_work.users.get_by_id = AsyncMock(return_value=recipient)
    unit_of_work.users.mark_blocked = AsyncMock()
    unit_of_work.windows.get = AsyncMock(return_value=window())
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    unit_of_work.features.get = AsyncMock(
        return_value=SimpleNamespace(reminders=True, reviews=True, repeat_booking=True)
    )
    unit_of_work.reviews.get_for_appointment = AsyncMock(return_value=None)
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work, target_job, recipient


@pytest.mark.asyncio
async def test_review_request_is_delivered_only_for_completed_without_existing_review() -> None:
    target_job = job()
    target_job.notification_type = NotificationType.REVIEW_REQUEST
    unit_of_work, target_job, _ = build_uow(
        target_job=target_job,
        appointment_status=AppointmentStatus.COMPLETED,
    )
    service = NotificationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    delivery = await service.prepare_delivery(21, "worker-1", now=NOW)
    assert delivery is not None
    assert delivery.notification_type is NotificationType.REVIEW_REQUEST

    unit_of_work.reviews.get_for_appointment.return_value = object()
    cancelled = await service.prepare_delivery(21, "worker-1", now=NOW)
    assert cancelled is None
    assert target_job.status is NotificationJobStatus.CANCELLED


@pytest.mark.asyncio
async def test_repeat_reminder_requires_live_marketing_consent_and_no_future_booking() -> None:
    target_job = job()
    target_job.notification_type = NotificationType.REPEAT_BOOKING_REMINDER
    unit_of_work, target_job, recipient = build_uow(
        target_job=target_job,
        appointment_status=AppointmentStatus.COMPLETED,
    )
    recipient.marketing_consent_at = NOW
    recipient.repeat_booking_opt_out_at = None
    unit_of_work.services.get = AsyncMock(return_value=SimpleNamespace(is_active=True))
    service = NotificationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    assert await service.prepare_delivery(21, "worker-1", now=NOW) is not None

    unit_of_work.appointments.has_future_active_for_client.return_value = True
    assert await service.prepare_delivery(21, "worker-1", now=NOW) is None
    assert target_job.last_error == "repeat_booking_not_actionable"


@pytest.mark.asyncio
async def test_claim_commits_short_lease_transaction() -> None:
    unit_of_work, _, _ = build_uow()
    service = NotificationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    claimed = await service.claim_due("worker-1", limit=20, now=NOW)

    assert claimed == [21]
    kwargs = unit_of_work.notifications.claim_due.await_args.kwargs
    assert kwargs["lease_expired_before"] == NOW - timedelta(seconds=120)
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_appointment_is_not_prepared_for_delivery() -> None:
    unit_of_work, target_job, _ = build_uow(
        appointment_status=AppointmentStatus.CANCELLED_BY_CLIENT
    )
    service = NotificationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    delivery = await service.prepare_delivery(21, "worker-1", now=NOW)

    assert delivery is None
    assert target_job.status is NotificationJobStatus.CANCELLED
    assert target_job.last_error == "appointment_inactive"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_reminders_are_cancelled_before_loading_client_data() -> None:
    unit_of_work, target_job, _ = build_uow()
    unit_of_work.features.get.return_value.reminders = False
    service = NotificationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    assert await service.prepare_delivery(21, "worker-1", now=NOW) is None

    assert target_job.status is NotificationJobStatus.CANCELLED
    assert target_job.last_error == "feature_disabled"
    unit_of_work.appointments.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_recipient_is_not_prepared_for_delivery() -> None:
    unit_of_work, target_job, _ = build_uow(recipient_blocked=True)
    service = NotificationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    delivery = await service.prepare_delivery(21, "worker-1", now=NOW)

    assert delivery is None
    assert target_job.status is NotificationJobStatus.FAILED
    assert target_job.last_error == "recipient_blocked"


@pytest.mark.asyncio
async def test_active_delivery_is_finalized_as_sent_by_lease_owner() -> None:
    unit_of_work, target_job, _ = build_uow()
    service = NotificationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    delivery = await service.prepare_delivery(21, "worker-1", now=NOW)
    marked = await service.mark_sent(21, "worker-1", now=NOW)

    assert delivery is not None
    assert delivery.recipient_telegram_id == 101
    assert marked
    assert target_job.status is NotificationJobStatus.SENT
    assert target_job.sent_at == NOW


@pytest.mark.asyncio
async def test_retry_is_delayed_and_attempt_limit_is_terminal() -> None:
    retry_uow, retry_job, _ = build_uow(target_job=job(attempts=2))
    retry_service = NotificationService(
        lambda: retry_uow,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    await retry_service.retry(
        21,
        "worker-1",
        delay_seconds=31,
        error_code="telegram_retry_after",
        now=NOW,
    )

    assert retry_job.status is NotificationJobStatus.PENDING
    assert retry_job.available_at == NOW + timedelta(seconds=31)

    failed_uow, failed_job, _ = build_uow(target_job=job(attempts=5))
    failed_service = NotificationService(
        lambda: failed_uow,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )
    await failed_service.retry(
        21,
        "worker-1",
        delay_seconds=31,
        error_code="temporary",
        now=NOW,
    )

    assert failed_job.status is NotificationJobStatus.FAILED
    assert failed_job.last_error == "attempts_exhausted"


@pytest.mark.asyncio
async def test_forbidden_recipient_is_persistently_marked_blocked() -> None:
    unit_of_work, target_job, recipient = build_uow()
    service = NotificationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    await service.mark_recipient_blocked(21, "worker-1")

    unit_of_work.users.mark_blocked.assert_awaited_once_with(recipient)
    assert target_job.status is NotificationJobStatus.FAILED
    assert target_job.last_error == "telegram_forbidden"
