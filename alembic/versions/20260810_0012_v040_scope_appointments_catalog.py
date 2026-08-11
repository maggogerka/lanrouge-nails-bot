"""Scope legacy data and add multi-master appointment/catalog fields.

Revision ID: 20260810_0012
Revises: 20260810_0011
Create Date: 2026-08-10 13:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0012"
down_revision: str | None = "20260810_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUSINESS_ROOTS = (
    "business_settings",
    "services",
    "availability_windows",
    "appointments",
    "notification_jobs",
    "audit_logs",
    "client_tags",
    "user_client_tags",
    "client_notes",
    "consent_history",
    "portfolio_items",
    "portfolio_tags",
    "reviews",
    "waitlist_entries",
    "waitlist_notifications",
    "broadcasts",
    "broadcast_recipients",
    "marketing_events",
    "appointment_reference_media",
    "reference_cleanup_state",
    "master_profiles",
    "master_public_links",
)

APPOINTMENT_STATUSES = (
    "pending_payment",
    "pending_manual_confirmation",
    "confirmed",
    "client_confirmed",
    "completed",
    "cancelled_by_client",
    "cancelled_by_admin",
    "no_show",
    "rescheduled",
    "payment_expired",
    "refund_pending",
    "partially_refunded",
    "refunded",
)

payment_mode = postgresql.ENUM(
    "disabled", "manual", "yookassa", name="payment_mode", create_type=False
)


def _replace_appointment_enum() -> None:
    """Replace the enum in one transaction so new values are immediately usable."""

    op.execute("ALTER TABLE appointments ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE appointments ALTER COLUMN status TYPE varchar(64) USING status::text"
    )
    op.execute(
        "ALTER TABLE appointment_status_history ALTER COLUMN previous_status "
        "TYPE varchar(64) USING previous_status::text"
    )
    op.execute(
        "ALTER TABLE appointment_status_history ALTER COLUMN new_status "
        "TYPE varchar(64) USING new_status::text"
    )
    op.execute("DROP TYPE appointment_status")
    values = ", ".join(f"'{value}'" for value in APPOINTMENT_STATUSES)
    op.execute(f"CREATE TYPE appointment_status AS ENUM ({values})")
    op.execute(
        "ALTER TABLE appointments ALTER COLUMN status TYPE appointment_status "
        "USING status::appointment_status"
    )
    op.execute(
        "ALTER TABLE appointment_status_history ALTER COLUMN previous_status "
        "TYPE appointment_status USING previous_status::appointment_status"
    )
    op.execute(
        "ALTER TABLE appointment_status_history ALTER COLUMN new_status "
        "TYPE appointment_status USING new_status::appointment_status"
    )
    op.execute("ALTER TABLE appointments ALTER COLUMN status SET DEFAULT 'confirmed'")


def _add_business_scope(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("business_id", sa.BigInteger(), server_default="1", nullable=False),
    )
    op.create_foreign_key(
        op.f(f"fk_{table_name}_business_id_businesses"),
        table_name,
        "businesses",
        ["business_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column(table_name, "business_id", server_default=None)


def upgrade() -> None:
    """Backfill Business 1 and StaffMember 1 before enforcing all constraints."""

    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    bind = op.get_bind()
    payment_mode.create(bind, checkfirst=True)

    op.drop_index("uq_appointments_occupied_window", table_name="appointments")
    _replace_appointment_enum()

    for table_name in BUSINESS_ROOTS:
        _add_business_scope(table_name)

    op.drop_constraint(
        op.f("ck_business_settings_singleton"), "business_settings", type_="check"
    )
    op.drop_constraint(
        op.f("ck_business_settings_booking_horizon_positive"),
        "business_settings",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_business_settings_booking_horizon_valid"),
        "business_settings",
        "booking_horizon_days BETWEEN 1 AND 365",
    )
    op.create_unique_constraint(
        "uq_business_settings_business_id", "business_settings", ["business_id"]
    )

    op.add_column(
        "availability_windows",
        sa.Column("staff_member_id", sa.BigInteger(), server_default="1", nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_availability_windows_staff_member_id_staff_members"),
        "availability_windows",
        "staff_members",
        ["staff_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("availability_windows", "staff_member_id", server_default=None)
    op.execute(
        "ALTER TABLE availability_windows "
        "DROP CONSTRAINT ex_availability_windows_active_overlap"
    )
    op.drop_index("ix_availability_windows_status_start", table_name="availability_windows")
    op.drop_index("ix_availability_windows_start", table_name="availability_windows")
    op.create_index(
        "ix_availability_windows_business_staff_status_start",
        "availability_windows",
        ["business_id", "staff_member_id", "status", "start_at"],
    )
    op.create_index(
        "ix_availability_windows_business_start",
        "availability_windows",
        ["business_id", "start_at"],
    )
    op.execute(
        """
        ALTER TABLE availability_windows
        ADD CONSTRAINT ex_availability_windows_active_overlap
        EXCLUDE USING gist (
            staff_member_id WITH =,
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (status IN ('open', 'reserved', 'booked'))
        """
    )

    op.add_column(
        "appointments",
        sa.Column("staff_member_id", sa.BigInteger(), server_default="1", nullable=False),
    )
    op.add_column(
        "appointments", sa.Column("master_name_snapshot", sa.String(length=255))
    )
    op.add_column(
        "appointments",
        sa.Column("prepayment_snapshot", sa.Numeric(12, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "appointments",
        sa.Column("currency_snapshot", sa.String(length=3), server_default="RUB", nullable=False),
    )
    op.add_column(
        "appointments",
        sa.Column("payment_mode_snapshot", payment_mode, server_default="disabled", nullable=False),
    )
    op.add_column("appointments", sa.Column("scheduled_start_at", sa.DateTime(timezone=True)))
    op.add_column("appointments", sa.Column("scheduled_end_at", sa.DateTime(timezone=True)))
    op.add_column(
        "appointments", sa.Column("reservation_expires_at", sa.DateTime(timezone=True))
    )
    op.execute(
        """
        UPDATE appointments AS appointment
        SET scheduled_start_at = booking_window.start_at,
            scheduled_end_at = booking_window.end_at,
            master_name_snapshot = staff.display_name
        FROM availability_windows AS booking_window, staff_members AS staff
        WHERE appointment.window_id = booking_window.id AND staff.id = 1
        """
    )
    op.alter_column("appointments", "scheduled_start_at", nullable=False)
    op.alter_column("appointments", "scheduled_end_at", nullable=False)
    op.alter_column("appointments", "master_name_snapshot", nullable=False)
    op.alter_column("appointments", "staff_member_id", server_default=None)
    op.create_foreign_key(
        op.f("fk_appointments_staff_member_id_staff_members"),
        "appointments",
        "staff_members",
        ["staff_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_appointments_scheduled_range_valid"),
        "appointments",
        "scheduled_start_at < scheduled_end_at",
    )
    op.create_check_constraint(
        op.f("ck_appointments_prepayment_snapshot_non_negative"),
        "appointments",
        "prepayment_snapshot >= 0",
    )
    op.create_check_constraint(
        op.f("ck_appointments_currency_snapshot_valid"),
        "appointments",
        "char_length(currency_snapshot) = 3",
    )
    op.drop_index("ix_appointments_client_status", table_name="appointments")
    op.create_index(
        "ix_appointments_business_client_status",
        "appointments",
        ["business_id", "client_id", "status"],
    )
    op.create_index(
        "ix_appointments_business_staff_start",
        "appointments",
        ["business_id", "staff_member_id", "scheduled_start_at"],
    )
    op.create_index(
        "uq_appointments_occupied_window",
        "appointments",
        ["window_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_payment', 'pending_manual_confirmation', 'confirmed', "
            "'client_confirmed', 'completed', 'no_show')"
        ),
    )
    op.execute(
        """
        ALTER TABLE appointments
        ADD CONSTRAINT ex_appointments_staff_active_overlap
        EXCLUDE USING gist (
            staff_member_id WITH =,
            tstzrange(scheduled_start_at, scheduled_end_at, '[)') WITH &&
        )
        WHERE (status IN (
            'pending_payment', 'pending_manual_confirmation', 'confirmed', 'client_confirmed'
        ))
        """
    )

    op.add_column(
        "services",
        sa.Column("prepayment_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
    )
    op.add_column(
        "services",
        sa.Column("online_booking_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column("services", sa.Column("telegram_photo_file_id", sa.String(length=512)))
    op.add_column(
        "services", sa.Column("telegram_photo_file_unique_id", sa.String(length=255))
    )
    op.add_column(
        "services", sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("services", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        op.f("ck_services_prepayment_non_negative"), "services", "prepayment_amount >= 0"
    )
    op.drop_index("ix_services_active_name", table_name="services")
    op.create_index(
        "ix_services_business_active_order",
        "services",
        ["business_id", "is_active", "sort_order"],
    )

    op.add_column(
        "portfolio_items",
        sa.Column("staff_member_id", sa.BigInteger(), server_default="1", nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_portfolio_items_staff_member_id_staff_members"),
        "portfolio_items",
        "staff_members",
        ["staff_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("portfolio_items", "staff_member_id", server_default=None)
    op.drop_index("ix_portfolio_items_status_order", table_name="portfolio_items")
    op.create_index(
        "ix_portfolio_items_business_staff_status_order",
        "portfolio_items",
        ["business_id", "staff_member_id", "status", "sort_order", "published_at"],
    )

    op.drop_constraint("uq_portfolio_tags_slug", "portfolio_tags", type_="unique")
    op.drop_index("uq_portfolio_tags_name_ci", table_name="portfolio_tags")
    op.create_index(
        "uq_portfolio_tags_business_slug",
        "portfolio_tags",
        ["business_id", "slug"],
        unique=True,
    )
    op.create_index(
        "uq_portfolio_tags_business_name_ci",
        "portfolio_tags",
        ["business_id", sa.text("lower(name)")],
        unique=True,
    )

    op.drop_index("uq_client_tags_name_ci", table_name="client_tags")
    op.create_index(
        "uq_client_tags_business_name_ci",
        "client_tags",
        ["business_id", sa.text("lower(name)")],
        unique=True,
    )
    op.drop_index("ix_client_notes_client_created", table_name="client_notes")
    op.create_index(
        "ix_client_notes_business_client_created",
        "client_notes",
        ["business_id", "client_id", "created_at"],
    )

    op.add_column(
        "consent_history",
        sa.Column(
            "policy_version", sa.String(length=64), server_default="legacy-unversioned", nullable=False
        ),
    )
    op.add_column("consent_history", sa.Column("policy_url", sa.String(length=2048)))
    op.add_column("consent_history", sa.Column("policy_hash", sa.String(length=64)))
    op.add_column("consent_history", sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.alter_column("consent_history", "policy_version", server_default=None)
    op.drop_index("ix_consent_history_user_created", table_name="consent_history")
    op.create_index(
        "ix_consent_history_business_user_created",
        "consent_history",
        ["business_id", "user_id", "created_at"],
    )

    op.add_column("waitlist_entries", sa.Column("preferred_staff_member_id", sa.BigInteger()))
    op.create_foreign_key(
        op.f("fk_waitlist_entries_preferred_staff_member_id_staff_members"),
        "waitlist_entries",
        "staff_members",
        ["preferred_staff_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        op.f("ck_master_profiles_ck_master_profiles_singleton"),
        "master_profiles",
        type_="check",
    )
    op.add_column(
        "master_profiles",
        sa.Column("staff_member_id", sa.BigInteger(), server_default="1", nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_master_profiles_staff_member_id_staff_members"),
        "master_profiles",
        "staff_members",
        ["staff_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("master_profiles", "staff_member_id", server_default=None)
    op.create_unique_constraint(
        "uq_master_profiles_business_staff",
        "master_profiles",
        ["business_id", "staff_member_id"],
    )

    op.drop_constraint(
        op.f("ck_reference_cleanup_state_ck_reference_cleanup_state_singleton"),
        "reference_cleanup_state",
        type_="check",
    )
    op.create_unique_constraint(
        "uq_reference_cleanup_state_business", "reference_cleanup_state", ["business_id"]
    )

    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.create_index(
        "ix_audit_logs_business_entity",
        "audit_logs",
        ["business_id", "entity_type", "entity_id"],
    )
    op.create_index(
        "ix_audit_logs_business_created_at", "audit_logs", ["business_id", "created_at"]
    )

    op.drop_index("ix_reviews_moderation_created", table_name="reviews")
    op.drop_index("ix_reviews_deleted_created", table_name="reviews")
    op.create_index(
        "ix_reviews_business_moderation_created",
        "reviews",
        ["business_id", "moderation_status", "created_at"],
    )
    op.create_index(
        "ix_reviews_business_deleted_created",
        "reviews",
        ["business_id", "deleted_at", "created_at"],
    )

    op.drop_index("ix_waitlist_entries_active_dates", table_name="waitlist_entries")
    op.drop_index("ix_waitlist_entries_client_status", table_name="waitlist_entries")
    op.drop_index("ix_waitlist_notifications_due", table_name="waitlist_notifications")
    op.create_index(
        "ix_waitlist_entries_business_active_dates",
        "waitlist_entries",
        ["business_id", "status", "date_from", "date_to"],
    )
    op.create_index(
        "ix_waitlist_entries_business_client_status",
        "waitlist_entries",
        ["business_id", "client_id", "status"],
    )
    op.create_index(
        "ix_waitlist_notifications_business_due",
        "waitlist_notifications",
        ["business_id", "status", "available_at"],
    )

    op.drop_index("ix_broadcasts_status_schedule", table_name="broadcasts")
    op.drop_index("ix_broadcast_recipients_due", table_name="broadcast_recipients")
    op.drop_index("ix_marketing_events_broadcast_created", table_name="marketing_events")
    op.create_index(
        "ix_broadcasts_business_status_schedule",
        "broadcasts",
        ["business_id", "status", "scheduled_at"],
    )
    op.create_index(
        "ix_broadcast_recipients_business_due",
        "broadcast_recipients",
        ["business_id", "status", "available_at"],
    )
    op.create_index(
        "ix_marketing_events_business_broadcast_created",
        "marketing_events",
        ["business_id", "broadcast_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade is intentionally blocked after tenant-scoped writes exist."""

    raise RuntimeError(
        "Downgrading v0.4 tenant scope is unsafe; restore the pre-migration v0.3.1 backup"
    )
