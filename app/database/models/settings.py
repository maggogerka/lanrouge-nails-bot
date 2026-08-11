"""Typed singleton business settings model."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import PortfolioDisplayMode


class BusinessSettings(TimestampMixin, Base):
    """Current mutable booking rules for one explicitly scoped business."""

    __tablename__ = "business_settings"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_business_settings_business_id"),
        CheckConstraint("booking_horizon_days BETWEEN 1 AND 365", name="booking_horizon_valid"),
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
        CheckConstraint(
            "availability_date_picker_days BETWEEN 1 AND 62",
            name="availability_date_picker_days_valid",
        ),
        CheckConstraint(
            "availability_time_step_minutes BETWEEN 1 AND 1440 "
            "AND MOD(1440, availability_time_step_minutes) = 0",
            name="availability_time_step_valid",
        ),
        CheckConstraint(
            "booking_reference_max_media BETWEEN 1 AND 10",
            name="booking_reference_max_media_valid",
        ),
        CheckConstraint(
            "booking_reference_edit_deadline_hours BETWEEN 1 AND 720",
            name="booking_reference_edit_deadline_valid",
        ),
        CheckConstraint(
            "booking_reference_retention_days IS NULL "
            "OR booking_reference_retention_days BETWEEN 1 AND 3650",
            name="booking_reference_retention_valid",
        ),
        CheckConstraint(
            "future_booking_limit_max BETWEEN 1 AND 100",
            name="future_booking_limit_max_valid",
        ),
        CheckConstraint(
            "future_booking_limit_horizon_days BETWEEN 1 AND 365",
            name="future_booking_limit_horizon_valid",
        ),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
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
    availability_date_picker_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=31, server_default="31"
    )
    availability_time_step_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    booking_reference_max_media: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    booking_reference_edit_deadline_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=36, server_default="36"
    )
    booking_reference_retention_days: Mapped[int | None] = mapped_column(Integer)
    future_booking_limit_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    future_booking_limit_max: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default="4"
    )
    future_booking_limit_horizon_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    future_booking_count_client_cancellations: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    portfolio_mode: Mapped[PortfolioDisplayMode] = mapped_column(
        database_enum(PortfolioDisplayMode, name="portfolio_display_mode"),
        nullable=False,
        default=PortfolioDisplayMode.INTERNAL,
        server_default=PortfolioDisplayMode.INTERNAL.value,
    )
    external_portfolio_url: Mapped[str | None] = mapped_column(String(2048))
    external_portfolio_button_text: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Открыть портфолио", server_default="Открыть портфолио"
    )
    master_profile_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
