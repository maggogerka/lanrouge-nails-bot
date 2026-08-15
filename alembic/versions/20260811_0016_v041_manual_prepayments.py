"""Add an explicit, concurrency-safe manual prepayment lifecycle.

Revision ID: 20260811_0016
Revises: 20260810_0015
Create Date: 2026-08-11 12:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260811_0016"
down_revision: str | None = "20260810_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

manual_payment_status = postgresql.ENUM(
    "awaiting_payment",
    "client_reported",
    "review_pending",
    "confirmed",
    "rejected",
    "expired",
    "cancelled",
    name="manual_payment_status",
    create_type=False,
)


def upgrade() -> None:
    # PostgreSQL does not allow a newly added enum value to be used by an index
    # predicate until the ALTER TYPE transaction has committed.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE reservation_status ADD VALUE IF NOT EXISTS 'awaiting_review'")
        op.execute("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'payment_due_client'")
        op.execute("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'payment_review_staff'")
    manual_payment_status.create(op.get_bind(), checkfirst=True)
    op.execute("UPDATE services SET prepayment_amount = price WHERE prepayment_amount > price")
    op.create_check_constraint(
        op.f("ck_services_prepayment_within_price"),
        "services",
        "prepayment_amount <= price",
    )
    op.add_column(
        "business_payment_settings",
        sa.Column(
            "client_payment_reminder_minutes",
            postgresql.JSONB(),
            server_default=sa.text("'[5, 10]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "business_payment_settings",
        sa.Column(
            "staff_review_reminder_minutes",
            postgresql.JSONB(),
            server_default=sa.text("'[30, 120]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "business_payment_settings",
        sa.Column(
            "client_payment_reminders_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "business_payment_settings",
        sa.Column(
            "staff_payment_notifications_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column("payments", sa.Column("manual_status", manual_payment_status))
    op.add_column("payments", sa.Column("client_reported_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("review_started_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("reviewed_by_user_id", sa.BigInteger()))
    op.add_column("payments", sa.Column("rejection_reason", sa.Text()))
    op.add_column("payments", sa.Column("receipt_file_id", sa.Text()))
    op.add_column("payments", sa.Column("receipt_file_unique_id", sa.String(length=255)))
    op.add_column("payments", sa.Column("receipt_media_type", sa.String(length=16)))
    op.add_column("payments", sa.Column("receipt_file_size", sa.BigInteger()))
    op.add_column("payments", sa.Column("receipt_received_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("receipt_expires_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        op.f("fk_payments_reviewed_by_user_id_users"),
        "payments",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_payments_receipt_file_size_positive"),
        "payments",
        "receipt_file_size IS NULL OR receipt_file_size > 0",
    )
    op.create_check_constraint(
        op.f("ck_payments_receipt_media_type_valid"),
        "payments",
        "receipt_media_type IS NULL OR receipt_media_type IN ('photo', 'document')",
    )
    op.execute(
        """
        UPDATE payments
        SET manual_status = CASE
            WHEN status = 'succeeded' THEN 'confirmed'::manual_payment_status
            WHEN status = 'cancelled' THEN 'cancelled'::manual_payment_status
            WHEN status = 'failed' THEN 'rejected'::manual_payment_status
            ELSE 'awaiting_payment'::manual_payment_status
        END
        WHERE provider = 'manual'
        """
    )
    op.create_check_constraint(
        op.f("ck_payments_manual_status_provider_consistent"),
        "payments",
        "(provider = 'manual' AND manual_status IS NOT NULL) OR "
        "(provider <> 'manual' AND manual_status IS NULL)",
    )
    op.execute(
        """
        UPDATE appointments
        SET status = 'pending_payment'
        WHERE payment_mode_snapshot = 'manual'
          AND status = 'pending_manual_confirmation'
          AND EXISTS (
              SELECT 1 FROM payments
              WHERE payments.appointment_id = appointments.id
                AND payments.provider = 'manual'
                AND payments.manual_status = 'awaiting_payment'
          )
        """
    )
    op.drop_index("uq_booking_reservations_active_window", table_name="booking_reservations")
    op.create_index(
        "uq_booking_reservations_active_window",
        "booking_reservations",
        ["business_id", "window_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'awaiting_review')"),
    )
    op.create_index("ix_payments_manual_status", "payments", ["business_id", "manual_status"])
    op.create_index(
        "uq_payments_manual_appointment",
        "payments",
        ["business_id", "appointment_id"],
        unique=True,
        postgresql_where=sa.text("provider = 'manual'"),
    )
    op.alter_column("business_payment_settings", "reservation_ttl_minutes", server_default="15")


def downgrade() -> None:
    op.drop_column("business_payment_settings", "staff_payment_notifications_enabled")
    op.drop_column("business_payment_settings", "client_payment_reminders_enabled")
    op.drop_column("business_payment_settings", "staff_review_reminder_minutes")
    op.drop_column("business_payment_settings", "client_payment_reminder_minutes")
    op.drop_constraint(op.f("ck_services_prepayment_within_price"), "services", type_="check")
    op.alter_column("business_payment_settings", "reservation_ttl_minutes", server_default="20")
    op.drop_index("uq_payments_manual_appointment", table_name="payments")
    op.drop_index("ix_payments_manual_status", table_name="payments")
    op.drop_index("uq_booking_reservations_active_window", table_name="booking_reservations")
    op.execute("UPDATE booking_reservations SET status = 'active' WHERE status = 'awaiting_review'")
    op.create_index(
        "uq_booking_reservations_active_window",
        "booking_reservations",
        ["business_id", "window_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_constraint(op.f("ck_payments_receipt_media_type_valid"), "payments", type_="check")
    op.drop_constraint(op.f("ck_payments_receipt_file_size_positive"), "payments", type_="check")
    op.drop_constraint(
        op.f("ck_payments_manual_status_provider_consistent"), "payments", type_="check"
    )
    op.drop_constraint(
        op.f("fk_payments_reviewed_by_user_id_users"), "payments", type_="foreignkey"
    )
    for column in (
        "receipt_expires_at",
        "receipt_received_at",
        "receipt_file_size",
        "receipt_media_type",
        "receipt_file_unique_id",
        "receipt_file_id",
        "rejection_reason",
        "reviewed_by_user_id",
        "reviewed_at",
        "review_started_at",
        "client_reported_at",
        "manual_status",
    ):
        op.drop_column("payments", column)
    manual_payment_status.drop(op.get_bind(), checkfirst=True)
