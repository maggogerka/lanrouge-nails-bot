"""Repeat booking uses the latest completion and current service catalog values."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import Appointment, Service, User
from app.domain.enums import AppointmentStatus
from app.domain.errors import RepeatBookingStateError
from app.schemas.booking import ClientActor
from app.services.repeat_booking_service import RepeatBookingService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def completed() -> Appointment:
    return Appointment(
        id=11,
        client_id=5,
        window_id=7,
        service_id=3,
        service_name_snapshot="Маникюр",
        price_snapshot=Decimal("2200"),
        duration_min_snapshot=120,
        duration_max_snapshot=180,
        status=AppointmentStatus.COMPLETED,
    )


def catalog_service(*, active: bool = True) -> Service:
    return Service(
        id=3,
        name="Маникюр и покрытие",
        price=Decimal("2500"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        is_active=active,
    )


def build_uow(*, previous: Appointment | None = None, active: bool = True) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_by_telegram_id = AsyncMock(
        return_value=User(id=5, telegram_id=101, privacy_consent_at=NOW)
    )
    unit_of_work.appointments.latest_completed_for_client = AsyncMock(return_value=previous)
    unit_of_work.services.get = AsyncMock(return_value=catalog_service(active=active))
    unit_of_work.settings.get = AsyncMock(
        return_value=SimpleNamespace(master_telegram_url="https://t.me/master")
    )
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_repeat_offer_uses_current_price_and_reports_change() -> None:
    unit_of_work = build_uow(previous=completed())
    service = RepeatBookingService(lambda: unit_of_work)  # type: ignore[arg-type]

    offer = await service.get_offer(ClientActor(telegram_id=101))

    assert offer.previous_price == Decimal("2200")
    assert offer.current_price == Decimal("2500")
    assert offer.price_changed
    assert offer.service_name == "Маникюр и покрытие"


@pytest.mark.asyncio
async def test_no_completed_visit_has_no_repeat_offer() -> None:
    unit_of_work = build_uow(previous=None)
    service = RepeatBookingService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(RepeatBookingStateError):
        await service.get_offer(ClientActor(telegram_id=101))


@pytest.mark.asyncio
async def test_archived_service_is_never_presented_as_bookable() -> None:
    unit_of_work = build_uow(previous=completed(), active=False)
    service = RepeatBookingService(lambda: unit_of_work)  # type: ignore[arg-type]

    offer = await service.get_offer(ClientActor(telegram_id=101))
    assert not offer.service_active


@pytest.mark.asyncio
async def test_repeat_opt_out_is_independent_from_marketing_consent() -> None:
    unit_of_work = build_uow(previous=completed())
    client = await unit_of_work.users.get_by_telegram_id(101)
    client.marketing_consent_at = NOW
    unit_of_work.users.get_by_telegram_id.return_value = client
    service = RepeatBookingService(lambda: unit_of_work)  # type: ignore[arg-type]

    await service.opt_out(ClientActor(telegram_id=101), now=NOW)

    assert client.repeat_booking_opt_out_at == NOW
    assert client.marketing_consent_at == NOW
    unit_of_work.commit.assert_awaited_once()
