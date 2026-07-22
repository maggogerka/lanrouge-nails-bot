"""Add waitlist requests, match delivery and moderated reviews.

Revision ID: 20260722_0004
Revises: 20260722_0003
Create Date: 2026-07-22 22:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260722_0004"
down_revision: str | None = "20260722_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

waitlist_status = postgresql.ENUM(
    "active", "matched", "booked", "cancelled", "expired", name="waitlist_status", create_type=False
)
waitlist_notification_status = postgresql.ENUM(
    "pending",
    "processing",
    "sent",
    "retry",
    "failed",
    "cancelled",
    name="waitlist_notification_status",
    create_type=False,
)
review_moderation_status = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    "hidden",
    name="review_moderation_status",
    create_type=False,
)


def upgrade() -> None:
    """Create explicit request and review records with database invariants."""

    bind = op.get_bind()
    waitlist_status.create(bind, checkfirst=True)
    waitlist_notification_status.create(bind, checkfirst=True)
    review_moderation_status.create(bind, checkfirst=True)

    op.create_table(
        "waitlist_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column(
            "preferred_dates",
            postgresql.ARRAY(sa.Date()),
            server_default=sa.text("'{}'::date[]"),
            nullable=False,
        ),
        sa.Column("preferred_time_from", sa.Time()),
        sa.Column("preferred_time_to", sa.Time()),
        sa.Column("status", waitlist_status, server_default="active", nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("booked_appointment_id", sa.BigInteger()),
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
        sa.CheckConstraint(
            "date_from <= date_to", name="ck_waitlist_entries_date_range_valid"
        ),
        sa.CheckConstraint(
            "(preferred_time_from IS NULL) = (preferred_time_to IS NULL)",
            name="ck_waitlist_entries_preferred_time_pair_valid",
        ),
        sa.CheckConstraint(
            "preferred_time_from IS NULL OR preferred_time_from < preferred_time_to",
            name="ck_waitlist_entries_preferred_time_range_valid",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["users.id"],
            name="fk_waitlist_entries_client_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_waitlist_entries_service_id_services",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["booked_appointment_id"],
            ["appointments.id"],
            name="fk_waitlist_entries_booked_appointment_id_appointments",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_waitlist_entries"),
    )
    op.create_index(
        "ix_waitlist_entries_active_dates",
        "waitlist_entries",
        ["status", "date_from", "date_to"],
    )
    op.create_index(
        "ix_waitlist_entries_client_status", "waitlist_entries", ["client_id", "status"]
    )

    op.create_table(
        "waitlist_notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("waitlist_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("window_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", waitlist_notification_status, server_default="pending", nullable=False
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(length=128)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(length=1000)),
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
        sa.CheckConstraint(
            "attempts >= 0", name="ck_waitlist_notifications_attempts_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["waitlist_entry_id"],
            ["waitlist_entries.id"],
            name="fk_waitlist_notifications_waitlist_entry_id_waitlist_entries",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["window_id"],
            ["availability_windows.id"],
            name="fk_waitlist_notifications_window_id_availability_windows",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_waitlist_notifications"),
        sa.UniqueConstraint(
            "waitlist_entry_id", "window_id", name="uq_waitlist_notifications_match"
        ),
    )
    op.create_index(
        "ix_waitlist_notifications_due", "waitlist_notifications", ["status", "available_at"]
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("appointment_id", sa.BigInteger(), nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column(
            "publication_consent", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "moderation_status", review_moderation_status, server_default="pending", nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating_valid"),
        sa.CheckConstraint(
            "text IS NULL OR char_length(text) <= 2000", name="ck_reviews_text_length_valid"
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="fk_reviews_appointment_id_appointments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["users.id"], name="fk_reviews_client_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
        sa.UniqueConstraint("appointment_id", name="uq_reviews_appointment_id"),
    )
    op.create_index(
        "ix_reviews_moderation_created", "reviews", ["moderation_status", "created_at"]
    )


def downgrade() -> None:
    """Drop waitlist and review records in reverse dependency order."""

    op.drop_index("ix_reviews_moderation_created", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("ix_waitlist_notifications_due", table_name="waitlist_notifications")
    op.drop_table("waitlist_notifications")
    op.drop_index("ix_waitlist_entries_client_status", table_name="waitlist_entries")
    op.drop_index("ix_waitlist_entries_active_dates", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")

    bind = op.get_bind()
    review_moderation_status.drop(bind, checkfirst=True)
    waitlist_notification_status.drop(bind, checkfirst=True)
    waitlist_status.drop(bind, checkfirst=True)
