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
    column,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import AppointmentStatus, PaymentMode


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
        CheckConstraint("scheduled_start_at < scheduled_end_at", name="scheduled_range_valid"),
        CheckConstraint("prepayment_snapshot >= 0", name="prepayment_snapshot_non_negative"),
        CheckConstraint("char_length(currency_snapshot) = 3", name="currency_snapshot_valid"),
        ExcludeConstraint(
            (column("staff_member_id"), "="),
            (
                func.tstzrange(column("scheduled_start_at"), column("scheduled_end_at"), "[)"),
                "&&",
            ),
            where=text(
                "status IN ('pending_payment', 'pending_manual_confirmation', "
                "'confirmed', 'client_confirmed')"
            ),
            using="gist",
            name="ex_appointments_staff_active_overlap",
        ),
        ExcludeConstraint(
            (column("workstation_id"), "="),
            (
                func.tstzrange(column("scheduled_start_at"), column("scheduled_end_at"), "[)"),
                "&&",
            ),
            where=text(
                "workstation_id IS NOT NULL AND "
                "status IN ('pending_payment', 'pending_manual_confirmation', "
                "'confirmed', 'client_confirmed')"
            ),
            using="gist",
            name="ex_appointments_workstation_active_overlap",
        ),
        Index("ix_appointments_business_client_status", "business_id", "client_id", "status"),
        Index(
            "ix_appointments_business_staff_start",
            "business_id",
            "staff_member_id",
            "scheduled_start_at",
        ),
        Index("ix_appointments_window", "window_id"),
        Index(
            "ix_appointments_business_workstation_start",
            "business_id",
            "workstation_id",
            "scheduled_start_at",
        ),
        Index("ix_appointments_design_reference", "design_reference_id"),
        Index(
            "uq_appointments_occupied_window",
            "window_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending_payment', 'pending_manual_confirmation', "
                "'confirmed', 'client_confirmed', 'completed', 'no_show')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    staff_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
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
    workstation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("workstations.id", ondelete="RESTRICT"),
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
    master_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    address_snapshot: Mapped[str | None] = mapped_column(String(500))
    map_url_snapshot: Mapped[str | None] = mapped_column(String(2048))
    master_contact_url_snapshot: Mapped[str | None] = mapped_column(String(2048))
    price_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    prepayment_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    currency_snapshot: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default="RUB"
    )
    payment_mode_snapshot: Mapped[PaymentMode] = mapped_column(
        database_enum(PaymentMode, name="payment_mode"),
        nullable=False,
        default=PaymentMode.DISABLED,
        server_default=PaymentMode.DISABLED.value,
    )
    duration_min_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_max_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reservation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
