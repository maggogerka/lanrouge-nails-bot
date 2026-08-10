"""Add payment providers, refunds, bounded webhooks, reservations, and subscriptions.

Revision ID: 20260810_0014
Revises: 20260810_0013
Create Date: 2026-08-10 15:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0014"
down_revision: str | None = "20260810_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

payment_mode = postgresql.ENUM(
    "disabled", "manual", "yookassa", name="payment_mode", create_type=False
)
payment_status = postgresql.ENUM(
    "created",
    "pending",
    "succeeded",
    "cancelled",
    "failed",
    "refund_pending",
    "partially_refunded",
    "refunded",
    name="payment_status",
    create_type=False,
)
refund_status = postgresql.ENUM(
    "pending", "succeeded", "failed", "cancelled", name="refund_status", create_type=False
)
payment_type = postgresql.ENUM("deposit", "full_payment", name="payment_type", create_type=False)
webhook_processing_status = postgresql.ENUM(
    "pending", "processed", "ignored", "failed", name="webhook_processing_status", create_type=False
)
reservation_status = postgresql.ENUM(
    "active", "consumed", "expired", "cancelled", name="reservation_status", create_type=False
)
subscription_provider = postgresql.ENUM(
    "manual", "external", name="subscription_provider", create_type=False
)
subscription_status = postgresql.ENUM(
    "trial",
    "active",
    "past_due",
    "suspended",
    "cancelled",
    name="subscription_status",
    create_type=False,
)


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (
        payment_status,
        refund_status,
        payment_type,
        webhook_processing_status,
        reservation_status,
        subscription_provider,
        subscription_status,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "business_payment_settings",
        sa.Column("business_id", sa.BigInteger(), primary_key=True),
        sa.Column("mode", payment_mode, server_default="disabled", nullable=False),
        sa.Column("provider_account_ref", sa.String(length=128)),
        sa.Column("manual_payment_instructions", sa.Text()),
        sa.Column("reservation_ttl_minutes", sa.Integer(), server_default="20", nullable=False),
        sa.Column(
            "cancellation_refund_deadline_hours", sa.Integer(), server_default="24", nullable=False
        ),
        sa.Column(
            "late_cancellation_refund_percent", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "reservation_ttl_minutes BETWEEN 5 AND 60",
            name=op.f("ck_business_payment_settings_reservation_ttl_valid"),
        ),
        sa.CheckConstraint(
            "cancellation_refund_deadline_hours BETWEEN 0 AND 8760",
            name=op.f("ck_business_payment_settings_refund_deadline_valid"),
        ),
        sa.CheckConstraint(
            "late_cancellation_refund_percent BETWEEN 0 AND 100",
            name=op.f("ck_business_payment_settings_late_refund_percent_valid"),
        ),
        sa.CheckConstraint(
            "version > 0", name=op.f("ck_business_payment_settings_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="RESTRICT",
            name=op.f("fk_business_payment_settings_business_id_businesses"),
        ),
    )
    op.execute("INSERT INTO business_payment_settings (business_id, mode) VALUES (1, 'disabled')")

    op.create_table(
        "booking_reservations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_member_id", sa.BigInteger(), nullable=False),
        sa.Column("window_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("appointment_id", sa.BigInteger()),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", reservation_status, server_default="active", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.String(length=64)),
        *_timestamps(),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_booking_reservations_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "char_length(token_digest) = 64",
            name=op.f("ck_booking_reservations_token_digest_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="RESTRICT",
            name=op.f("fk_booking_reservations_business_id_businesses"),
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_booking_reservations_client_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["staff_member_id"],
            ["staff_members.id"],
            ondelete="RESTRICT",
            name=op.f("fk_booking_reservations_staff_member_id_staff_members"),
        ),
        sa.ForeignKeyConstraint(
            ["window_id"],
            ["availability_windows.id"],
            ondelete="RESTRICT",
            name=op.f("fk_booking_reservations_window_id_availability_windows"),
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            ondelete="RESTRICT",
            name=op.f("fk_booking_reservations_service_id_services"),
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            ondelete="RESTRICT",
            name=op.f("fk_booking_reservations_appointment_id_appointments"),
        ),
        sa.UniqueConstraint("token_digest", name="uq_booking_reservations_token_digest"),
    )
    op.create_index(
        "uq_booking_reservations_active_window",
        "booking_reservations",
        ["business_id", "window_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_booking_reservations_business_idempotency",
        "booking_reservations",
        ["business_id", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_booking_reservations_expiry",
        "booking_reservations",
        ["status", "expires_at"],
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("appointment_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", payment_mode, nullable=False),
        sa.Column("provider_account_ref", sa.String(length=128)),
        sa.Column("provider_payment_id", sa.String(length=128)),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("refunded_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", payment_status, server_default="created", nullable=False),
        sa.Column("payment_type", payment_type, nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("confirmation_url", sa.String(length=2048)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("refunded_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("correlation_id", sa.String(length=64)),
        *_timestamps(),
        sa.CheckConstraint("provider <> 'disabled'", name=op.f("ck_payments_provider_enabled")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payments_amount_positive")),
        sa.CheckConstraint(
            "refunded_amount >= 0", name=op.f("ck_payments_refunded_amount_non_negative")
        ),
        sa.CheckConstraint(
            "refunded_amount <= amount", name=op.f("ck_payments_refunded_amount_within_payment")
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_payments_currency_valid")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_payments_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="RESTRICT",
            name=op.f("fk_payments_business_id_businesses"),
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            ondelete="RESTRICT",
            name=op.f("fk_payments_appointment_id_appointments"),
        ),
        sa.UniqueConstraint(
            "business_id",
            "provider",
            "provider_payment_id",
            name="uq_payments_provider_payment",
        ),
        sa.UniqueConstraint(
            "business_id", "idempotency_key", name="uq_payments_business_idempotency"
        ),
    )
    op.create_index("ix_payments_appointment_status", "payments", ["appointment_id", "status"])
    op.create_index("ix_payments_business_status", "payments", ["business_id", "status"])
    op.create_index(
        "ix_payments_expiry",
        "payments",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )

    op.create_table(
        "refunds",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", payment_mode, nullable=False),
        sa.Column("provider_refund_id", sa.String(length=128)),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", refund_status, server_default="pending", nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("requested_by_user_id", sa.BigInteger()),
        sa.Column("succeeded_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("correlation_id", sa.String(length=64)),
        *_timestamps(),
        sa.CheckConstraint("provider <> 'disabled'", name=op.f("ck_refunds_provider_enabled")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_refunds_amount_positive")),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_refunds_currency_valid")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_refunds_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="RESTRICT",
            name=op.f("fk_refunds_business_id_businesses"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            ondelete="RESTRICT",
            name=op.f("fk_refunds_payment_id_payments"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_refunds_requested_by_user_id_users"),
        ),
        sa.UniqueConstraint(
            "business_id",
            "provider",
            "provider_refund_id",
            name="uq_refunds_provider_refund",
        ),
        sa.UniqueConstraint(
            "business_id", "idempotency_key", name="uq_refunds_business_idempotency"
        ),
    )
    op.create_index("ix_refunds_payment_status", "refunds", ["payment_id", "status"])

    op.create_table(
        "payment_webhook_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_id", sa.BigInteger()),
        sa.Column("provider", payment_mode, nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider_object_id", sa.String(length=128), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", webhook_processing_status, server_default="pending", nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("correlation_id", sa.String(length=64)),
        sa.CheckConstraint(
            "provider <> 'disabled'", name=op.f("ck_payment_webhook_events_provider_enabled")
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_payment_webhook_events_attempts_non_negative")
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_payment_webhook_events_payload_digest_valid"),
        ),
        sa.CheckConstraint(
            "event_key ~ '^[0-9a-f]{64}$'", name=op.f("ck_payment_webhook_events_event_key_valid")
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="RESTRICT",
            name=op.f("fk_payment_webhook_events_business_id_businesses"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            ondelete="RESTRICT",
            name=op.f("fk_payment_webhook_events_payment_id_payments"),
        ),
        sa.UniqueConstraint(
            "business_id",
            "provider",
            "event_key",
            name="uq_payment_webhook_events_business_event",
        ),
    )
    op.create_index(
        "ix_payment_webhook_pending",
        "payment_webhook_events",
        ["status", "received_at"],
    )
    op.create_index("ix_payment_webhook_expiry", "payment_webhook_events", ["expires_at"])

    op.create_table(
        "business_subscriptions",
        sa.Column("business_id", sa.BigInteger(), primary_key=True),
        sa.Column("provider", subscription_provider, server_default="manual", nullable=False),
        sa.Column("status", subscription_status, server_default="trial", nullable=False),
        sa.Column("plan_code", sa.String(length=64), server_default="standard", nullable=False),
        sa.Column("external_customer_ref", sa.String(length=128)),
        sa.Column("external_subscription_ref", sa.String(length=128)),
        sa.Column("monthly_price_snapshot", sa.Numeric(12, 2)),
        sa.Column("currency_snapshot", sa.String(length=3)),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True)),
        sa.Column("current_period_ends_at", sa.DateTime(timezone=True)),
        sa.Column("grace_ends_at", sa.DateTime(timezone=True)),
        sa.Column("next_payment_at", sa.DateTime(timezone=True)),
        sa.Column("blocking_reason_code", sa.String(length=64)),
        sa.Column(
            "feature_limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="RESTRICT",
            name=op.f("fk_business_subscriptions_business_id_businesses"),
        ),
        sa.CheckConstraint(
            "plan_code ~ '^[a-z0-9][a-z0-9_-]{0,63}$'",
            name=op.f("ck_business_subscriptions_plan_code_format"),
        ),
    )
    op.execute(
        "INSERT INTO business_subscriptions (business_id, provider, status) "
        "VALUES (1, 'manual', 'active')"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrading payments can lose financial audit data; restore the v0.3.1 backup"
    )
