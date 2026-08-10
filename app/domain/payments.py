"""Provider-independent payment invariants and lifecycle transitions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from app.domain.enums import PaymentStatus, RefundStatus
from app.domain.errors import DomainError

_CURRENCY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")
_METADATA_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FORBIDDEN_METADATA_FRAGMENTS: Final[tuple[str, ...]] = (
    "authorization",
    "card",
    "comment",
    "cookie",
    "cvv",
    "email",
    "name",
    "password",
    "payload",
    "phone",
    "secret",
    "token",
)
MAX_SAFE_METADATA_ITEMS: Final[int] = 16
MAX_SAFE_METADATA_VALUE_LENGTH: Final[int] = 256
MAX_MONEY: Final[Decimal] = Decimal("9999999999.99")


class PaymentType(StrEnum):
    """Whether a charge covers a deposit or the full service price."""

    DEPOSIT = "deposit"
    FULL_PAYMENT = "full_payment"


class WebhookProcessingStatus(StrEnum):
    """Bounded inbox state; the raw webhook body is never persisted."""

    PENDING = "pending"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


class PaymentStateError(DomainError):
    """A payment or refund operation violates its lifecycle contract."""


_PAYMENT_TRANSITIONS: Final[dict[PaymentStatus, frozenset[PaymentStatus]]] = {
    PaymentStatus.CREATED: frozenset(
        {
            PaymentStatus.PENDING,
            PaymentStatus.SUCCEEDED,
            PaymentStatus.CANCELLED,
            PaymentStatus.FAILED,
        }
    ),
    PaymentStatus.PENDING: frozenset(
        {PaymentStatus.SUCCEEDED, PaymentStatus.CANCELLED, PaymentStatus.FAILED}
    ),
    PaymentStatus.SUCCEEDED: frozenset(
        {
            PaymentStatus.REFUND_PENDING,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        }
    ),
    PaymentStatus.REFUND_PENDING: frozenset(
        {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        }
    ),
    PaymentStatus.PARTIALLY_REFUNDED: frozenset(
        {PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED}
    ),
    PaymentStatus.CANCELLED: frozenset(),
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.REFUNDED: frozenset(),
}

_REFUND_TRANSITIONS: Final[dict[RefundStatus, frozenset[RefundStatus]]] = {
    RefundStatus.PENDING: frozenset(
        {RefundStatus.SUCCEEDED, RefundStatus.FAILED, RefundStatus.CANCELLED}
    ),
    RefundStatus.SUCCEEDED: frozenset(),
    RefundStatus.FAILED: frozenset(),
    RefundStatus.CANCELLED: frozenset(),
}


def require_payment_transition(current: PaymentStatus, target: PaymentStatus) -> None:
    """Allow explicit transitions and harmless replay of the current state."""

    if current is target:
        return
    if target not in _PAYMENT_TRANSITIONS[current]:
        raise PaymentStateError(f"Недопустимый переход платежа: {current} -> {target}.")


def require_refund_transition(current: RefundStatus, target: RefundStatus) -> None:
    """Allow one-way refund transitions and idempotent provider replays."""

    if current is target:
        return
    if target not in _REFUND_TRANSITIONS[current]:
        raise PaymentStateError(f"Недопустимый переход возврата: {current} -> {target}.")


def validate_money(amount: Decimal, currency: str) -> tuple[Decimal, str]:
    """Validate exact two-decimal money without silently rounding provider data."""

    if not amount.is_finite() or amount <= 0 or amount > MAX_MONEY:
        raise ValueError("amount must be positive and fit Numeric(12, 2)")
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise ValueError("amount must have at most two decimal places")
    normalized_currency = currency.strip().upper()
    if not _CURRENCY_PATTERN.fullmatch(normalized_currency):
        raise ValueError("currency must be a three-letter ISO-style code")
    return amount.quantize(Decimal("0.01")), normalized_currency


def validate_safe_metadata(values: Mapping[str, str]) -> dict[str, str]:
    """Accept only a small flat allow-list-shaped mapping without likely PII/secrets."""

    if len(values) > MAX_SAFE_METADATA_ITEMS:
        raise ValueError(f"safe metadata may contain at most {MAX_SAFE_METADATA_ITEMS} items")
    normalized: dict[str, str] = {}
    for key, value in values.items():
        normalized_key = key.strip().casefold()
        if not _METADATA_KEY_PATTERN.fullmatch(normalized_key):
            raise ValueError(
                "safe metadata keys must use lowercase letters, digits and underscores"
            )
        if any(fragment in normalized_key for fragment in _FORBIDDEN_METADATA_FRAGMENTS):
            raise ValueError("safe metadata key may contain personal or secret data")
        normalized_value = value.strip()
        if not normalized_value or len(normalized_value) > MAX_SAFE_METADATA_VALUE_LENGTH:
            raise ValueError("safe metadata values must be non-empty and at most 256 characters")
        normalized[normalized_key] = normalized_value
    return normalized


def aware_utc(value: datetime | None = None) -> datetime:
    """Return an aware UTC timestamp and reject ambiguous naive input."""

    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return current.astimezone(UTC)
