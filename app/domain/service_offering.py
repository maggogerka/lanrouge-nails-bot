"""Pure resolution of base and per-staff service commercial terms."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.errors import WindowValidationError


@dataclass(frozen=True, slots=True)
class BaseServiceTerms:
    price: Decimal
    duration_min_minutes: int
    duration_max_minutes: int
    prepayment_amount: Decimal | None = None
    prepayment_percent: Decimal | None = None
    online_booking_enabled: bool = True


@dataclass(frozen=True, slots=True)
class StaffServiceOverrides:
    price: Decimal | None = None
    duration_min_minutes: int | None = None
    duration_max_minutes: int | None = None
    prepayment_amount: Decimal | None = None
    prepayment_percent: Decimal | None = None
    online_booking_enabled: bool = True
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class EffectiveServiceTerms:
    price: Decimal
    duration_min_minutes: int
    duration_max_minutes: int
    prepayment_amount: Decimal | None
    prepayment_percent: Decimal | None
    online_booking_enabled: bool


def resolve_service_terms(
    base: BaseServiceTerms, overrides: StaffServiceOverrides
) -> EffectiveServiceTerms:
    """Apply one staff assignment without mutating historical/base service data."""

    duration_min = (
        overrides.duration_min_minutes
        if overrides.duration_min_minutes is not None
        else base.duration_min_minutes
    )
    duration_max = (
        overrides.duration_max_minutes
        if overrides.duration_max_minutes is not None
        else base.duration_max_minutes
    )
    price = overrides.price if overrides.price is not None else base.price
    if overrides.prepayment_amount is not None or overrides.prepayment_percent is not None:
        prepayment_amount = overrides.prepayment_amount
        prepayment_percent = overrides.prepayment_percent
    else:
        prepayment_amount = base.prepayment_amount
        prepayment_percent = base.prepayment_percent

    if price < 0 or not 0 < duration_min <= duration_max:
        raise WindowValidationError("Resolved service terms are invalid")
    if prepayment_amount is not None and prepayment_amount < 0:
        raise WindowValidationError("Prepayment amount cannot be negative")
    if prepayment_percent is not None and not Decimal("0") <= prepayment_percent <= Decimal("100"):
        raise WindowValidationError("Prepayment percent must be between 0 and 100")
    if prepayment_amount is not None and prepayment_percent is not None:
        raise WindowValidationError("Only one prepayment kind can be configured")

    return EffectiveServiceTerms(
        price=price,
        duration_min_minutes=duration_min,
        duration_max_minutes=duration_max,
        prepayment_amount=prepayment_amount,
        prepayment_percent=prepayment_percent,
        online_booking_enabled=(
            base.online_booking_enabled and overrides.online_booking_enabled and overrides.is_active
        ),
    )
