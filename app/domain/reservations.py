"""Reservation tokens, lifecycle rules and safe result values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from secrets import token_urlsafe
from typing import Final

from pydantic import SecretStr

from app.domain.enums import ReservationStatus
from app.domain.errors import BookingUnavailableError

_TOKEN_MIN_LENGTH: Final[int] = 32


class ReservationStateError(BookingUnavailableError):
    """A reservation is absent, expired or in an incompatible lifecycle state."""


class ReservationExpiryAction(StrEnum):
    """Non-sensitive worker outcome for one locked reservation."""

    EXPIRED = "expired"
    RECONCILED = "reconciled"


@dataclass(frozen=True, slots=True)
class ReservationToken:
    """One-time raw token plus the only representation safe for persistence."""

    raw: SecretStr
    digest: str

    @classmethod
    def issue(cls) -> ReservationToken:
        return cls.from_raw(token_urlsafe(32))

    @classmethod
    def from_raw(cls, raw: str) -> ReservationToken:
        if len(raw) < _TOKEN_MIN_LENGTH:
            raise ValueError("reservation token must contain at least 32 characters")
        return cls(raw=SecretStr(raw), digest=sha256(raw.encode()).hexdigest())


_TRANSITIONS: Final[dict[ReservationStatus, frozenset[ReservationStatus]]] = {
    ReservationStatus.ACTIVE: frozenset(
        {
            ReservationStatus.AWAITING_REVIEW,
            ReservationStatus.CONSUMED,
            ReservationStatus.EXPIRED,
            ReservationStatus.CANCELLED,
        }
    ),
    ReservationStatus.AWAITING_REVIEW: frozenset(
        {ReservationStatus.CONSUMED, ReservationStatus.CANCELLED}
    ),
    ReservationStatus.CONSUMED: frozenset(),
    ReservationStatus.EXPIRED: frozenset(),
    ReservationStatus.CANCELLED: frozenset(),
}


def ensure_reservation_transition(current: ReservationStatus, target: ReservationStatus) -> None:
    """Validate a one-way reservation transition with idempotent replay."""

    if current is target:
        return
    if target not in _TRANSITIONS[current]:
        raise ReservationStateError("Резерв уже завершён и не может изменить статус.")


@dataclass(frozen=True, slots=True)
class ReservationExpiryResult:
    checked: int
    expired: int
    reconciled_paid: int
    errors: int
