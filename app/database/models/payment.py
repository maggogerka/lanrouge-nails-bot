"""Payment, refund and bounded webhook-inbox persistence models."""

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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.domain.payments import PaymentType, WebhookProcessingStatus


class Payment(TimestampMixin, Base):
    """One provider-independent charge intent for an appointment."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("provider <> 'disabled'", name="provider_enabled"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("refunded_amount >= 0", name="refunded_amount_non_negative"),
        CheckConstraint("refunded_amount <= amount", name="refunded_amount_within_payment"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_valid"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        UniqueConstraint(
            "business_id",
            "provider",
            "provider_payment_id",
            name="uq_payments_provider_payment",
        ),
        UniqueConstraint(
            "business_id",
            "idempotency_key",
            name="uq_payments_business_idempotency",
        ),
        Index("ix_payments_appointment_status", "appointment_id", "status"),
        Index("ix_payments_business_status", "business_id", "status"),
        Index("ix_payments_expiry", "expires_at", postgresql_where=text("expires_at IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[PaymentMode] = mapped_column(
        database_enum(PaymentMode, name="payment_mode"), nullable=False
    )
    provider_account_ref: Mapped[str | None] = mapped_column(String(128))
    provider_payment_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    refunded_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        database_enum(PaymentStatus, name="payment_status"),
        nullable=False,
        default=PaymentStatus.CREATED,
        server_default=PaymentStatus.CREATED.value,
    )
    payment_type: Mapped[PaymentType] = mapped_column(
        database_enum(PaymentType, name="payment_type"), nullable=False
    )
    safe_metadata: Mapped[dict[str, str]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    confirmation_url: Mapped[str | None] = mapped_column(String(2048))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    correlation_id: Mapped[str | None] = mapped_column(String(64))


class Refund(TimestampMixin, Base):
    """An idempotent full or partial refund request."""

    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("provider <> 'disabled'", name="provider_enabled"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_valid"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        UniqueConstraint(
            "business_id",
            "provider",
            "provider_refund_id",
            name="uq_refunds_provider_refund",
        ),
        UniqueConstraint(
            "business_id",
            "idempotency_key",
            name="uq_refunds_business_idempotency",
        ),
        Index("ix_refunds_payment_status", "payment_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    payment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[PaymentMode] = mapped_column(
        database_enum(PaymentMode, name="payment_mode"), nullable=False
    )
    provider_refund_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[RefundStatus] = mapped_column(
        database_enum(RefundStatus, name="refund_status"),
        nullable=False,
        default=RefundStatus.PENDING,
        server_default=RefundStatus.PENDING.value,
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_metadata: Mapped[dict[str, str]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    requested_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    correlation_id: Mapped[str | None] = mapped_column(String(64))


class PaymentWebhookEvent(Base):
    """Deduplicated webhook metadata with mandatory expiry and no raw payload."""

    __tablename__ = "payment_webhook_events"
    __table_args__ = (
        CheckConstraint("provider <> 'disabled'", name="provider_enabled"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="payload_digest_valid"),
        CheckConstraint("event_key ~ '^[0-9a-f]{64}$'", name="event_key_valid"),
        UniqueConstraint(
            "business_id",
            "provider",
            "event_key",
            name="uq_payment_webhook_events_business_event",
        ),
        Index("ix_payment_webhook_pending", "status", "received_at"),
        Index("ix_payment_webhook_expiry", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    payment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payments.id", ondelete="RESTRICT")
    )
    provider: Mapped[PaymentMode] = mapped_column(
        database_enum(PaymentMode, name="payment_mode"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[WebhookProcessingStatus] = mapped_column(
        database_enum(WebhookProcessingStatus, name="webhook_processing_status"),
        nullable=False,
        default=WebhookProcessingStatus.PENDING,
        server_default=WebhookProcessingStatus.PENDING.value,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
