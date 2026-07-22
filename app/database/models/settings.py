"""Typed singleton business settings model."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, SmallInteger, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin


class BusinessSettings(TimestampMixin, Base):
    """Current mutable rules for the single-studio MVP."""

    __tablename__ = "business_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("booking_horizon_days > 0", name="booking_horizon_positive"),
        CheckConstraint(
            "cancellation_deadline_hours > 0",
            name="cancellation_deadline_positive",
        ),
        CheckConstraint(
            "max_appointments_per_day > 0",
            name="max_appointments_per_day_positive",
        ),
        CheckConstraint(
            "default_window_duration_minutes > 0",
            name="default_window_duration_positive",
        ),
        CheckConstraint("minimum_gap_minutes >= 0", name="minimum_gap_non_negative"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    map_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    master_telegram_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    booking_horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    cancellation_deadline_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    max_appointments_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    default_window_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_gap_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    allow_saturday: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allow_sunday: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reminder_offsets_minutes: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
