"""Availability DTO validation tests."""

from datetime import date, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain.enums import AvailabilityWindowStatus
from app.schemas.availability import AvailabilityWindowCreate


def test_create_normalizes_optional_comment() -> None:
    values = AvailabilityWindowCreate(
        local_date=date(2026, 7, 23),
        local_start_time=time(10),
        service_id=1,
        admin_comment="  служебная заметка  ",
    )

    assert values.admin_comment == "служебная заметка"


def test_create_rejects_persisted_non_admin_status() -> None:
    with pytest.raises(ValidationError, match="open or closed"):
        AvailabilityWindowCreate(
            local_date=date(2026, 7, 23),
            local_start_time=time(10),
            service_id=1,
            status=AvailabilityWindowStatus.BOOKED,
        )


def test_create_rejects_aware_wall_clock_time() -> None:
    with pytest.raises(ValidationError, match="must not contain a timezone"):
        AvailabilityWindowCreate(
            local_date=date(2026, 7, 23),
            local_start_time=time(10, tzinfo=timezone(timedelta(hours=3))),
            service_id=1,
        )
