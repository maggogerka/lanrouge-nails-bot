"""Short-lived, Telegram-user-scoped records for the public demo runtime."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin


class DemoSession(TimestampMixin, Base):
    """One isolated demo workspace owned by exactly one Telegram account."""

    __tablename__ = "demo_sessions"
    __table_args__ = (
        CheckConstraint("generation > 0", name="positive_generation"),
        Index("ix_demo_sessions_expiry", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DemoService(TimestampMixin, Base):
    __tablename__ = "demo_services"
    __table_args__ = (
        UniqueConstraint("session_id", "name", name="uq_demo_services_session_name"),
        CheckConstraint("duration_minutes BETWEEN 15 AND 480", name="duration_range"),
        CheckConstraint("price >= 0", name="non_negative_price"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class DemoStaff(TimestampMixin, Base):
    __tablename__ = "demo_staff"
    __table_args__ = (UniqueConstraint("session_id", "name", name="uq_demo_staff_session_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class DemoClient(TimestampMixin, Base):
    __tablename__ = "demo_clients"
    __table_args__ = (
        UniqueConstraint("session_id", "display_name", name="uq_demo_clients_session_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)


class DemoSlot(TimestampMixin, Base):
    __tablename__ = "demo_slots"
    __table_args__ = (
        UniqueConstraint("session_id", "staff_id", "start_at", name="uq_demo_slots_staff_start"),
        CheckConstraint("end_at > start_at", name="positive_interval"),
        Index("ix_demo_slots_session_available_start", "session_id", "is_available", "start_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_sessions.id", ondelete="CASCADE"), nullable=False
    )
    staff_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_staff.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_services.id", ondelete="CASCADE"), nullable=False
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class DemoAppointment(TimestampMixin, Base):
    __tablename__ = "demo_appointments"
    __table_args__ = (
        UniqueConstraint("slot_id", name="uq_demo_appointments_slot"),
        CheckConstraint("end_at > start_at", name="positive_interval"),
        Index("ix_demo_appointments_session_start", "session_id", "start_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_sessions.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_clients.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_services.id", ondelete="RESTRICT"), nullable=False
    )
    staff_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_staff.id", ondelete="RESTRICT"), nullable=False
    )
    slot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("demo_slots.id", ondelete="RESTRICT"), nullable=False
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="confirmed")
