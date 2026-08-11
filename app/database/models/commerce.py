"""Booking reservations, business payment policy, and CRM subscription state."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import (
    PaymentMode,
    ReservationStatus,
    SubscriptionProvider,
    SubscriptionStatus,
)


class BusinessPaymentSettings(TimestampMixin, Base):
    """Safe payment policy; provider credentials are never stored in the database."""

    __tablename__ = "business_payment_settings"
    __table_args__ = (
        CheckConstraint("reservation_ttl_minutes BETWEEN 5 AND 60", name="reservation_ttl_valid"),
        CheckConstraint(
            "cancellation_refund_deadline_hours BETWEEN 0 AND 8760",
            name="refund_deadline_valid",
        ),
        CheckConstraint(
            "late_cancellation_refund_percent BETWEEN 0 AND 100",
            name="late_refund_percent_valid",
        ),
        CheckConstraint("version > 0", name="version_positive"),
    )

    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), primary_key=True
    )
    mode: Mapped[PaymentMode] = mapped_column(
        database_enum(PaymentMode, name="payment_mode"),
        nullable=False,
        default=PaymentMode.DISABLED,
        server_default=PaymentMode.DISABLED.value,
    )
    provider_account_ref: Mapped[str | None] = mapped_column(String(128))
    manual_payment_instructions: Mapped[str | None] = mapped_column(Text)
    client_payment_reminder_minutes: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=lambda: [5, 10], server_default=text("'[5, 10]'::jsonb")
    )
    staff_review_reminder_minutes: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=lambda: [30, 120], server_default=text("'[30, 120]'::jsonb")
    )
    client_payment_reminders_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    staff_payment_notifications_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    reservation_ttl_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=15, server_default="15"
    )
    cancellation_refund_deadline_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24, server_default="24"
    )
    late_cancellation_refund_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")


class BookingReservation(TimestampMixin, Base):
    """A short-lived, idempotent hold used before payment or manual confirmation."""

    __tablename__ = "booking_reservations"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("char_length(token_digest) = 64", name="token_digest_valid"),
        Index(
            "uq_booking_reservations_active_window",
            "business_id",
            "window_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'awaiting_review')"),
        ),
        Index(
            "uq_booking_reservations_business_idempotency",
            "business_id",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_booking_reservations_expiry",
            "status",
            "expires_at",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    staff_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
    window_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("availability_windows.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("appointments.id", ondelete="RESTRICT")
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        database_enum(ReservationStatus, name="reservation_status"),
        nullable=False,
        default=ReservationStatus.ACTIVE,
        server_default=ReservationStatus.ACTIVE.value,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str | None] = mapped_column(String(64))


class BusinessSubscription(TimestampMixin, Base):
    """CRM billing state, intentionally separate from client service payments."""

    __tablename__ = "business_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "plan_code ~ '^[a-z0-9][a-z0-9_-]{0,63}$'",
            name="plan_code_format",
        ),
    )

    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), primary_key=True
    )
    provider: Mapped[SubscriptionProvider] = mapped_column(
        database_enum(SubscriptionProvider, name="subscription_provider"),
        nullable=False,
        default=SubscriptionProvider.MANUAL,
        server_default=SubscriptionProvider.MANUAL.value,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        database_enum(SubscriptionStatus, name="subscription_status"),
        nullable=False,
        default=SubscriptionStatus.TRIAL,
        server_default=SubscriptionStatus.TRIAL.value,
    )
    plan_code: Mapped[str] = mapped_column(
        String(64), nullable=False, default="standard", server_default="standard"
    )
    external_customer_ref: Mapped[str | None] = mapped_column(String(128))
    external_subscription_ref: Mapped[str | None] = mapped_column(String(128))
    monthly_price_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency_snapshot: Mapped[str | None] = mapped_column(String(3))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_payment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocking_reason_code: Mapped[str | None] = mapped_column(String(64))
    feature_limits: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
