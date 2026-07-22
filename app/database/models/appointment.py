"""Appointment and immutable status-history models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import AppointmentStatus


class Appointment(TimestampMixin, Base):
    """A booking with a snapshot that survives future service edits."""

    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("price_snapshot >= 0", name="price_snapshot_non_negative"),
        CheckConstraint("duration_min_snapshot > 0", name="duration_min_snapshot_positive"),
        CheckConstraint("duration_max_snapshot > 0", name="duration_max_snapshot_positive"),
        CheckConstraint(
            "duration_min_snapshot <= duration_max_snapshot",
            name="duration_snapshot_range_valid",
        ),
        Index("ix_appointments_client_status", "client_id", "status"),
        Index("ix_appointments_window", "window_id"),
        Index("ix_appointments_design_reference", "design_reference_id"),
        Index(
            "uq_appointments_occupied_window",
            "window_id",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('cancelled_by_client', 'cancelled_by_admin', 'rescheduled')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    window_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("availability_windows.id", ondelete="RESTRICT"),
        nullable=False,
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="RESTRICT"),
        nullable=False,
    )
    design_reference_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("portfolio_items.id", ondelete="RESTRICT"),
    )
    design_title_snapshot: Mapped[str | None] = mapped_column(String(255))
    rescheduled_from_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        unique=True,
    )
    service_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    price_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    duration_min_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_max_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        database_enum(AppointmentStatus, name="appointment_status"),
        nullable=False,
        default=AppointmentStatus.CONFIRMED,
        server_default=AppointmentStatus.CONFIRMED.value,
    )
    client_comment: Mapped[str | None] = mapped_column(Text)
    client_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    no_show_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)


class AppointmentStatusHistory(Base):
    """Append-only record of every appointment status transition."""

    __tablename__ = "appointment_status_history"
    __table_args__ = (Index("ix_appointment_status_history_appointment", "appointment_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    previous_status: Mapped[AppointmentStatus | None] = mapped_column(
        database_enum(AppointmentStatus, name="appointment_status"),
    )
    new_status: Mapped[AppointmentStatus] = mapped_column(
        database_enum(AppointmentStatus, name="appointment_status"),
        nullable=False,
    )
    changed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
