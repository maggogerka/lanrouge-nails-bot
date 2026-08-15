"""Waitlist validation, matching, cancellation and reliable delivery tests."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.database.models import (
    AvailabilityWindow,
    BusinessSettings,
    Service,
    User,
    WaitlistEntry,
    WaitlistNotification,
)
from app.domain.enums import (
    AvailabilityWindowStatus,
    WaitlistNotificationStatus,
    WaitlistStatus,
)
from app.schemas.booking import ClientActor
from app.schemas.service import AdminActor
from app.schemas.waitlist import WaitlistCreate
from app.services.waitlist_delivery_service import WaitlistDeliveryService
from app.services.waitlist_matching import enqueue_waitlist_matches
from app.services.waitlist_service import WaitlistService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def settings() -> BusinessSettings:
    return BusinessSettings(
        id=1,
        business_name="Example Studio",
        timezone="Europe/Moscow",
        address="Test address",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/example",
        booking_horizon_days=31,
        cancellation_deadline_hours=36,
        max_appointments_per_day=2,
        default_window_duration_minutes=210,
        minimum_gap_minutes=60,
        allow_saturday=True,
        allow_sunday=True,
        reminder_offsets_minutes=[1440, 180, 60],
        waitlist_default_expiration_days=31,
        waitlist_notification_cooldown_minutes=180,
        waitlist_enabled=True,
        version=1,
    )


def entry(status: WaitlistStatus = WaitlistStatus.ACTIVE) -> WaitlistEntry:
    return WaitlistEntry(
        id=12,
        client_id=5,
        service_id=3,
        date_from=date(2026, 7, 23),
        date_to=date(2026, 7, 30),
        preferred_dates=[],
        status=status,
        expires_at=NOW + timedelta(days=8),
    )


def window(status: AvailabilityWindowStatus = AvailabilityWindowStatus.OPEN):
    return AvailabilityWindow(
        id=7,
        business_id=1,
        staff_member_id=1,
        service_id=3,
        workstation_id=1,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, tzinfo=UTC),
        status=status,
        created_by=9,
    )


def service() -> Service:
    return Service(
        id=3,
        name="Консультация",
        price=Decimal("2500"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        is_active=True,
    )


def uow() -> MagicMock:
    result = MagicMock()
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=None)
    result.commit = AsyncMock()
    result.session.flush = AsyncMock()
    result.audit.add = AsyncMock()
    result.features.get = AsyncMock(return_value=SimpleNamespace(waitlist=True))
    result.service_assignments.list_bookable_services_for_staff = AsyncMock(
        return_value=[(SimpleNamespace(), service())]
    )
    result.workstations.has_available = AsyncMock(return_value=True)
    return result


def test_waitlist_preferences_validate_date_and_time_ranges() -> None:
    with pytest.raises(ValidationError):
        WaitlistCreate(
            service_id=3,
            date_from=date(2026, 7, 30),
            date_to=date(2026, 7, 23),
        )
    with pytest.raises(ValidationError):
        WaitlistCreate(
            service_id=3,
            date_from=date(2026, 7, 23),
            date_to=date(2026, 7, 30),
            preferred_time_from=time(18),
            preferred_time_to=time(12),
        )


@pytest.mark.asyncio
async def test_matching_queues_each_entry_once_and_sets_cooldown() -> None:
    unit_of_work = uow()
    target = entry()
    unit_of_work.waitlist.list_matching = AsyncMock(return_value=[target])
    unit_of_work.waitlist.enqueue_match = AsyncMock(side_effect=[True, False])

    first = await enqueue_waitlist_matches(
        unit_of_work, window(), settings(), now=NOW, correlation_id="request-1"
    )
    second = await enqueue_waitlist_matches(
        unit_of_work, window(), settings(), now=NOW, correlation_id="request-2"
    )

    assert first == 1
    assert second == 0
    assert target.status is WaitlistStatus.MATCHED
    assert target.notified_at == NOW
    assert unit_of_work.waitlist.enqueue_match.await_count == 2


@pytest.mark.asyncio
async def test_closed_or_started_window_does_not_match() -> None:
    unit_of_work = uow()
    unit_of_work.waitlist.list_matching = AsyncMock(return_value=[entry()])

    assert (
        await enqueue_waitlist_matches(
            unit_of_work, window(AvailabilityWindowStatus.CLOSED), settings(), now=NOW
        )
        == 0
    )
    started = window()
    started.start_at = NOW
    assert await enqueue_waitlist_matches(unit_of_work, started, settings(), now=NOW) == 0
    unit_of_work.waitlist.list_matching.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_is_service_notification_and_does_not_require_marketing_consent() -> None:
    unit_of_work = uow()
    client = User(
        id=5,
        telegram_id=101,
        privacy_consent_at=NOW,
        marketing_consent_at=None,
        is_blocked=False,
    )
    unit_of_work.users.get_by_telegram_id = AsyncMock(return_value=client)
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    unit_of_work.services.get = AsyncMock(return_value=service())

    async def save(value: WaitlistEntry) -> WaitlistEntry:
        value.id = 12
        return value

    unit_of_work.waitlist.add = AsyncMock(side_effect=save)
    waitlist = WaitlistService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    created = await waitlist.create(
        ClientActor(telegram_id=101),
        WaitlistCreate(
            service_id=3,
            date_from=date(2026, 7, 23),
            date_to=date(2026, 7, 30),
        ),
        now=NOW,
    )

    assert created.id == 12
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_entry_cancels_unsent_notifications() -> None:
    unit_of_work = uow()
    client = User(id=5, telegram_id=101, privacy_consent_at=NOW, is_blocked=False)
    target = entry()
    unit_of_work.users.get_by_telegram_id = AsyncMock(return_value=client)
    unit_of_work.waitlist.get = AsyncMock(return_value=target)
    unit_of_work.waitlist.cancel_unsent = AsyncMock()
    unit_of_work.services.get = AsyncMock(return_value=service())
    waitlist = WaitlistService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    result = await waitlist.cancel_my(ClientActor(telegram_id=101), target.id)

    assert result.status is WaitlistStatus.CANCELLED
    unit_of_work.waitlist.cancel_unsent.assert_awaited_once_with(target.id)


@pytest.mark.asyncio
async def test_admin_request_lookup_is_direct_and_not_limited_to_first_list_page() -> None:
    unit_of_work = uow()
    target = entry()
    unit_of_work.waitlist.get = AsyncMock(return_value=target)
    unit_of_work.users.get_by_id = AsyncMock(
        return_value=User(id=5, telegram_id=101, first_name="Анна")
    )
    unit_of_work.services.get = AsyncMock(return_value=service())
    waitlist = WaitlistService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    result = await waitlist.get_admin(AdminActor(telegram_id=900), target.id)

    assert result.id == target.id
    assert result.client_telegram_id == 101
    unit_of_work.waitlist.get.assert_awaited_once_with(target.id, for_update=False)
    unit_of_work.waitlist.list_page.assert_not_called()


@pytest.mark.asyncio
async def test_delivery_revalidates_and_cancels_taken_window() -> None:
    unit_of_work = uow()
    job = WaitlistNotification(
        id=21,
        waitlist_entry_id=12,
        window_id=7,
        status=WaitlistNotificationStatus.PROCESSING,
        scheduled_at=NOW,
        available_at=NOW,
        attempts=1,
        locked_at=NOW,
        locked_by="worker",
    )
    unit_of_work.waitlist.get_notification = AsyncMock(return_value=job)
    unit_of_work.waitlist.get = AsyncMock(return_value=entry())
    unit_of_work.windows.get = AsyncMock(return_value=window(AvailabilityWindowStatus.BOOKED))
    delivery = WaitlistDeliveryService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    assert await delivery.prepare_delivery(21, "worker", now=NOW) is None
    assert job.status is WaitlistNotificationStatus.CANCELLED
    assert job.last_error == "match_inactive"


@pytest.mark.asyncio
async def test_delivery_claim_uses_restart_safe_lease() -> None:
    unit_of_work = uow()
    unit_of_work.waitlist.claim_due_notifications = AsyncMock(return_value=[SimpleNamespace(id=21)])
    delivery = WaitlistDeliveryService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    assert await delivery.claim_due("worker", limit=10, now=NOW) == [21]
    assert unit_of_work.waitlist.claim_due_notifications.await_args.kwargs[
        "lease_expired_before"
    ] == NOW - timedelta(seconds=120)


@pytest.mark.asyncio
async def test_disabled_waitlist_cancels_claim_before_loading_client_data() -> None:
    unit_of_work = uow()
    job = WaitlistNotification(
        id=21,
        waitlist_entry_id=12,
        window_id=7,
        status=WaitlistNotificationStatus.PROCESSING,
        scheduled_at=NOW,
        available_at=NOW,
        attempts=1,
        locked_at=NOW,
        locked_by="worker",
    )
    unit_of_work.waitlist.get_notification = AsyncMock(return_value=job)
    unit_of_work.waitlist.get = AsyncMock()
    unit_of_work.features.get.return_value.waitlist = False
    delivery = WaitlistDeliveryService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        lease_seconds=120,
        max_attempts=5,
    )

    assert await delivery.prepare_delivery(21, "worker", now=NOW) is None

    assert job.status is WaitlistNotificationStatus.CANCELLED
    assert job.last_error == "feature_disabled"
    unit_of_work.waitlist.get.assert_not_awaited()
