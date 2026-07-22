"""Waitlist preferences and reliable window-match delivery."""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import WaitlistNotificationStatus, WaitlistStatus


class WaitlistEntry(TimestampMixin, Base):
    """A client's explicit request for a range of acceptable windows."""

    __tablename__ = "waitlist_entries"
    __table_args__ = (
        CheckConstraint("date_from <= date_to", name="date_range_valid"),
        CheckConstraint(
            "(preferred_time_from IS NULL) = (preferred_time_to IS NULL)",
            name="preferred_time_pair_valid",
        ),
        CheckConstraint(
            "preferred_time_from IS NULL OR preferred_time_from < preferred_time_to",
            name="preferred_time_range_valid",
        ),
        Index("ix_waitlist_entries_active_dates", "status", "date_from", "date_to"),
        Index("ix_waitlist_entries_client_status", "client_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    preferred_dates: Mapped[list[date]] = mapped_column(
        ARRAY(Date), nullable=False, default=list, server_default="{}"
    )
    preferred_time_from: Mapped[time | None] = mapped_column(Time)
    preferred_time_to: Mapped[time | None] = mapped_column(Time)
    status: Mapped[WaitlistStatus] = mapped_column(
        database_enum(WaitlistStatus, name="waitlist_status"),
        nullable=False,
        default=WaitlistStatus.ACTIVE,
        server_default=WaitlistStatus.ACTIVE.value,
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    booked_appointment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("appointments.id", ondelete="RESTRICT")
    )


class WaitlistNotification(TimestampMixin, Base):
    """Retryable service notification for one request/window pair."""

    __tablename__ = "waitlist_notifications"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        UniqueConstraint("waitlist_entry_id", "window_id", name="uq_waitlist_notifications_match"),
        Index("ix_waitlist_notifications_due", "status", "available_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    waitlist_entry_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("waitlist_entries.id", ondelete="RESTRICT"), nullable=False
    )
    window_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("availability_windows.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[WaitlistNotificationStatus] = mapped_column(
        database_enum(WaitlistNotificationStatus, name="waitlist_notification_status"),
        nullable=False,
        default=WaitlistNotificationStatus.PENDING,
        server_default=WaitlistNotificationStatus.PENDING.value,
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
