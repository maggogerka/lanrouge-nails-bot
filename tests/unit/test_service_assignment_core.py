"""Service assignment schema and effective commercial-term tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from app.database.models.schedule import StaffScheduleException, StaffWeeklyInterval
from app.database.models.service_assignment import (
    ServiceCategory,
    StaffServiceAssignment,
)
from app.domain.errors import WindowValidationError
from app.domain.service_offering import (
    BaseServiceTerms,
    StaffServiceOverrides,
    resolve_service_terms,
)


def test_schedule_models_have_staff_scoped_database_exclusions() -> None:
    weekly_names = {
        constraint.name
        for constraint in StaffWeeklyInterval.__table__.constraints
        if isinstance(constraint, ExcludeConstraint)
    }
    exception_names = {
        constraint.name
        for constraint in StaffScheduleException.__table__.constraints
        if isinstance(constraint, ExcludeConstraint)
    }

    assert weekly_names == {"ex_staff_weekly_intervals_overlap"}
    assert exception_names == {"ex_staff_schedule_exceptions_overlap"}


def test_catalog_models_are_business_scoped_and_assignments_are_unique() -> None:
    assert "business_id" in ServiceCategory.__table__.columns
    assert "business_id" in StaffServiceAssignment.__table__.columns
    assert any(
        index.name == "uq_staff_service_assignments_business_staff_service" and index.unique
        for index in StaffServiceAssignment.__table__.indexes
    )


def test_staff_overrides_resolve_price_duration_and_prepayment() -> None:
    result = resolve_service_terms(
        BaseServiceTerms(
            price=Decimal("2500"),
            duration_min_minutes=90,
            duration_max_minutes=120,
            prepayment_percent=Decimal("20"),
        ),
        StaffServiceOverrides(
            price=Decimal("3000"),
            duration_min_minutes=120,
            duration_max_minutes=150,
            prepayment_amount=Decimal("1000"),
        ),
    )

    assert result.price == Decimal("3000")
    assert (result.duration_min_minutes, result.duration_max_minutes) == (120, 150)
    assert result.prepayment_amount == Decimal("1000")
    assert result.prepayment_percent is None
    assert result.online_booking_enabled


def test_disabled_assignment_cannot_be_exposed_online() -> None:
    result = resolve_service_terms(
        BaseServiceTerms(Decimal("2500"), 90, 120),
        StaffServiceOverrides(is_active=False),
    )

    assert not result.online_booking_enabled


def test_invalid_resolved_commercial_terms_are_rejected() -> None:
    with pytest.raises(WindowValidationError, match="Only one prepayment"):
        resolve_service_terms(
            BaseServiceTerms(Decimal("2500"), 90, 120),
            StaffServiceOverrides(
                prepayment_amount=Decimal("500"),
                prepayment_percent=Decimal("20"),
            ),
        )

    with pytest.raises(WindowValidationError, match="terms are invalid"):
        resolve_service_terms(
            BaseServiceTerms(Decimal("2500"), 90, 120),
            StaffServiceOverrides(
                duration_min_minutes=0,
                duration_max_minutes=120,
            ),
        )
