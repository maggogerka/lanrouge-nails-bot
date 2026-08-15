"""Persistent notification queue model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import NotificationJobStatus, NotificationType


class NotificationJob(TimestampMixin, Base):
    """A retryable reminder claimed by short worker leases."""

    __tablename__ = "notification_jobs"
    __table_args__ = (
        CheckConstraint("offset_minutes > 0", name="offset_positive"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        UniqueConstraint(
            "appointment_id",
            "recipient_user_id",
            "notification_type",
            "offset_minutes",
            name="uq_notification_jobs_delivery",
        ),
        Index("ix_notification_jobs_due", "status", "available_at"),
        Index("ix_notification_jobs_appointment", "appointment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    recipient_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    notification_type: Mapped[NotificationType] = mapped_column(
        database_enum(NotificationType, name="notification_type"),
        nullable=False,
    )
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[NotificationJobStatus] = mapped_column(
        database_enum(NotificationJobStatus, name="notification_job_status"),
        nullable=False,
        default=NotificationJobStatus.PENDING,
        server_default=NotificationJobStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
