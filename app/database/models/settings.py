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
        CheckConstraint("portfolio_page_size BETWEEN 1 AND 20", name="portfolio_page_size_valid"),
        CheckConstraint("portfolio_max_media BETWEEN 1 AND 10", name="portfolio_max_media_valid"),
        CheckConstraint(
            "waitlist_default_expiration_days BETWEEN 1 AND 180",
            name="waitlist_expiration_valid",
        ),
        CheckConstraint(
            "waitlist_notification_cooldown_minutes BETWEEN 0 AND 10080",
            name="waitlist_cooldown_valid",
        ),
        CheckConstraint(
            "review_request_delay_minutes BETWEEN 0 AND 10080",
            name="review_delay_valid",
        ),
        CheckConstraint(
            "repeat_booking_reminder_days BETWEEN 1 AND 365",
            name="repeat_reminder_days_valid",
        ),
        CheckConstraint(
            "broadcast_messages_per_second BETWEEN 1 AND 20",
            name="broadcast_rate_valid",
        ),
        CheckConstraint(
            "broadcast_max_media BETWEEN 0 AND 10",
            name="broadcast_max_media_valid",
        ),
        CheckConstraint(
            "broadcast_max_retries BETWEEN 0 AND 20",
            name="broadcast_max_retries_valid",
        ),
        CheckConstraint(
            "broadcast_retry_base_seconds BETWEEN 1 AND 3600",
            name="broadcast_retry_base_valid",
        ),
        CheckConstraint("client_page_size BETWEEN 1 AND 50", name="client_page_size_valid"),
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
    portfolio_page_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    portfolio_max_media: Mapped[int] = mapped_column(
        Integer, nullable=False, default=8, server_default="8"
    )
    waitlist_default_expiration_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=31, server_default="31"
    )
    waitlist_notification_cooldown_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=180, server_default="180"
    )
    review_request_delay_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    repeat_booking_reminder_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=28, server_default="28"
    )
    broadcast_messages_per_second: Mapped[int] = mapped_column(
        Integer, nullable=False, default=15, server_default="15"
    )
    broadcast_max_media: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    broadcast_max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    broadcast_retry_base_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=15, server_default="15"
    )
    client_page_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    reviews_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    waitlist_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    broadcasts_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    portfolio_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
