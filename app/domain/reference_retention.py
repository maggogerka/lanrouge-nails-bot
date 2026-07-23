"""UTC retention rules for Telegram-hosted appointment references."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.domain.enums import AppointmentStatus


class MutableReference(Protocol):
    telegram_file_id: str | None
    telegram_file_unique_id: str | None
    deleted_at: datetime | None
    last_deletion_error: str | None
    deletion_attempts: int


@dataclass(frozen=True, slots=True)
class ReferenceRetentionPolicy:
    completed_days: int = 30
    cancelled_days: int = 7
    no_show_days: int = 14

    def expires_at(
        self,
        *,
        status: AppointmentStatus,
        planned_end_at: datetime,
        completed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> datetime:
        """Return a timezone-aware UTC expiry without shortening future visits."""

        planned_end = self._utc(planned_end_at)
        if status is AppointmentStatus.COMPLETED:
            base = self._utc(completed_at) if completed_at is not None else planned_end
            return base + timedelta(days=self.completed_days)
        if status in {
            AppointmentStatus.CANCELLED_BY_CLIENT,
            AppointmentStatus.CANCELLED_BY_ADMIN,
        }:
            base = self._utc(cancelled_at) if cancelled_at is not None else planned_end
            return base + timedelta(days=self.cancelled_days)
        if status is AppointmentStatus.NO_SHOW:
            return planned_end + timedelta(days=self.no_show_days)
        return planned_end + timedelta(days=self.completed_days)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("retention timestamps must be timezone-aware")
        return value.astimezone(UTC)


def anonymize_reference(row: MutableReference, deleted_at: datetime) -> int:
    """Remove application-managed Telegram identifiers and return estimated bytes released."""

    file_id = getattr(row, "telegram_file_id", None)
    unique_id = getattr(row, "telegram_file_unique_id", None)
    released = len(file_id.encode()) if isinstance(file_id, str) else 0
    released += len(unique_id.encode()) if isinstance(unique_id, str) else 0
    row.telegram_file_id = None
    row.telegram_file_unique_id = None
    row.deleted_at = deleted_at
    row.last_deletion_error = None
    row.deletion_attempts = (row.deletion_attempts or 0) + 1
    return released
