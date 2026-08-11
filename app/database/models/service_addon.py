"""Optional service additions and immutable appointment snapshots."""

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
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin


class ServiceAddon(TimestampMixin, Base):
    """An independently managed addition belonging to exactly one service."""

    __tablename__ = "service_addons"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("duration_min_minutes > 0", name="duration_min_positive"),
        CheckConstraint("duration_max_minutes > 0", name="duration_max_positive"),
        CheckConstraint(
            "duration_min_minutes <= duration_max_minutes",
            name="duration_range_valid",
        ),
        Index(
            "ix_service_addons_business_service_order",
            "business_id",
            "service_id",
            "is_active",
            "sort_order",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    duration_min_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_max_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_photo_file_id: Mapped[str | None] = mapped_column(String(512))
    telegram_photo_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class AppointmentAddonSnapshot(Base):
    """Append-only commercial terms selected when an appointment is created."""

    __tablename__ = "appointment_addon_snapshots"
    __table_args__ = (
        CheckConstraint("price_snapshot >= 0", name="price_snapshot_non_negative"),
        CheckConstraint("duration_min_snapshot > 0", name="duration_min_snapshot_positive"),
        CheckConstraint("duration_max_snapshot > 0", name="duration_max_snapshot_positive"),
        CheckConstraint(
            "duration_min_snapshot <= duration_max_snapshot",
            name="duration_snapshot_range_valid",
        ),
        Index(
            "uq_appointment_addon_snapshots_appointment_addon",
            "appointment_id",
            "service_addon_id",
            unique=True,
        ),
        Index(
            "ix_appointment_addon_snapshots_business_appointment",
            "business_id",
            "appointment_id",
            "position",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False
    )
    service_addon_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("service_addons.id", ondelete="RESTRICT"), nullable=False
    )
    name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    description_snapshot: Mapped[str | None] = mapped_column(Text)
    price_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    duration_min_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_max_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
