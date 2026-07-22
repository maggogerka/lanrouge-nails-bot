"""Add v0.2 CRM core fields, settings, consent history, tags and notes.

Revision ID: 20260722_0002
Revises: 20260722_0001
Create Date: 2026-07-22 21:30:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260722_0002"
down_revision: str | None = "20260722_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

consent_type = postgresql.ENUM(
    "privacy", "marketing", "repeat_booking", name="consent_type", create_type=False
)
consent_source = postgresql.ENUM(
    "onboarding",
    "notification_settings",
    "admin",
    "system",
    name="consent_source",
    create_type=False,
)


def upgrade() -> None:
    """Extend v0.1 records without rewriting or deleting existing data."""

    op.execute("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'review_request'")
    op.execute("ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'repeat_booking_reminder'")

    bind = op.get_bind()
    consent_type.create(bind, checkfirst=True)
    consent_source.create(bind, checkfirst=True)

    op.add_column("users", sa.Column("repeat_booking_opt_out_at", sa.DateTime(timezone=True)))
    op.add_column(
        "users",
        sa.Column(
            "is_self_booking_blocked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("self_booking_blocked_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("self_booking_blocked_by", sa.BigInteger()))
    op.add_column("users", sa.Column("self_booking_block_reason", sa.String(length=500)))
    op.create_foreign_key(
        "fk_users_self_booking_blocked_by_users",
        "users",
        "users",
        ["self_booking_blocked_by"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column("appointments", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("appointments", sa.Column("no_show_at", sa.DateTime(timezone=True)))

    settings_columns = (
        sa.Column("portfolio_page_size", sa.Integer(), server_default="5", nullable=False),
        sa.Column("portfolio_max_media", sa.Integer(), server_default="8", nullable=False),
        sa.Column(
            "waitlist_default_expiration_days", sa.Integer(), server_default="31", nullable=False
        ),
        sa.Column(
            "waitlist_notification_cooldown_minutes",
            sa.Integer(),
            server_default="180",
            nullable=False,
        ),
        sa.Column(
            "review_request_delay_minutes", sa.Integer(), server_default="60", nullable=False
        ),
        sa.Column(
            "repeat_booking_reminder_days", sa.Integer(), server_default="28", nullable=False
        ),
        sa.Column(
            "broadcast_messages_per_second", sa.Integer(), server_default="15", nullable=False
        ),
        sa.Column("broadcast_max_media", sa.Integer(), server_default="5", nullable=False),
        sa.Column("broadcast_max_retries", sa.Integer(), server_default="5", nullable=False),
        sa.Column(
            "broadcast_retry_base_seconds", sa.Integer(), server_default="15", nullable=False
        ),
        sa.Column("client_page_size", sa.Integer(), server_default="10", nullable=False),
        sa.Column("reviews_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("waitlist_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "broadcasts_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("portfolio_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    for column in settings_columns:
        op.add_column("business_settings", column)

    settings_checks = (
        ("ck_business_settings_portfolio_page_size_valid", "portfolio_page_size BETWEEN 1 AND 20"),
        ("ck_business_settings_portfolio_max_media_valid", "portfolio_max_media BETWEEN 1 AND 10"),
        (
            "ck_business_settings_waitlist_expiration_valid",
            "waitlist_default_expiration_days BETWEEN 1 AND 180",
        ),
        (
            "ck_business_settings_waitlist_cooldown_valid",
            "waitlist_notification_cooldown_minutes BETWEEN 0 AND 10080",
        ),
        (
            "ck_business_settings_review_delay_valid",
            "review_request_delay_minutes BETWEEN 0 AND 10080",
        ),
        (
            "ck_business_settings_repeat_reminder_days_valid",
            "repeat_booking_reminder_days BETWEEN 1 AND 365",
        ),
        (
            "ck_business_settings_broadcast_rate_valid",
            "broadcast_messages_per_second BETWEEN 1 AND 20",
        ),
        (
            "ck_business_settings_broadcast_max_media_valid",
            "broadcast_max_media BETWEEN 0 AND 10",
        ),
        (
            "ck_business_settings_broadcast_max_retries_valid",
            "broadcast_max_retries BETWEEN 0 AND 20",
        ),
        (
            "ck_business_settings_broadcast_retry_base_valid",
            "broadcast_retry_base_seconds BETWEEN 1 AND 3600",
        ),
        ("ck_business_settings_client_page_size_valid", "client_page_size BETWEEN 1 AND 50"),
    )
    for name, condition in settings_checks:
        op.create_check_constraint(name, "business_settings", condition)

    op.create_table(
        "client_tags",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("marker", sa.String(length=32)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_client_tags"),
    )
    op.create_index("uq_client_tags_name_ci", "client_tags", [sa.text("lower(name)")], unique=True)

    op.create_table(
        "user_client_tags",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.Column("assigned_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_client_tags_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["client_tags.id"],
            name="fk_user_client_tags_tag_id_client_tags",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["users.id"],
            name="fk_user_client_tags_assigned_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", "tag_id", name="pk_user_client_tags"),
    )

    op.create_table(
        "client_notes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
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
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 2000",
            name="ck_client_notes_text_length_valid",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["users.id"], name="fk_client_notes_client_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_client_notes_author_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_notes"),
    )
    op.create_index("ix_client_notes_client_created", "client_notes", ["client_id", "created_at"])

    op.create_table(
        "consent_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("consent_type", consent_type, nullable=False),
        sa.Column("previous_value", sa.Boolean()),
        sa.Column("new_value", sa.Boolean(), nullable=False),
        sa.Column("source", consent_source, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_consent_history_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_consent_history"),
    )
    op.create_index(
        "ix_consent_history_user_created", "consent_history", ["user_id", "created_at"]
    )


def downgrade() -> None:
    """Remove CRM core only when new notification kinds have no persisted jobs."""

    op.drop_index("ix_consent_history_user_created", table_name="consent_history")
    op.drop_table("consent_history")
    op.drop_index("ix_client_notes_client_created", table_name="client_notes")
    op.drop_table("client_notes")
    op.drop_table("user_client_tags")
    op.drop_index("uq_client_tags_name_ci", table_name="client_tags")
    op.drop_table("client_tags")

    checks = (
        "ck_business_settings_client_page_size_valid",
        "ck_business_settings_broadcast_retry_base_valid",
        "ck_business_settings_broadcast_max_retries_valid",
        "ck_business_settings_broadcast_max_media_valid",
        "ck_business_settings_broadcast_rate_valid",
        "ck_business_settings_repeat_reminder_days_valid",
        "ck_business_settings_review_delay_valid",
        "ck_business_settings_waitlist_cooldown_valid",
        "ck_business_settings_waitlist_expiration_valid",
        "ck_business_settings_portfolio_max_media_valid",
        "ck_business_settings_portfolio_page_size_valid",
    )
    for name in checks:
        op.drop_constraint(name, "business_settings", type_="check")
    columns = (
        "portfolio_enabled",
        "broadcasts_enabled",
        "waitlist_enabled",
        "reviews_enabled",
        "client_page_size",
        "broadcast_retry_base_seconds",
        "broadcast_max_retries",
        "broadcast_max_media",
        "broadcast_messages_per_second",
        "repeat_booking_reminder_days",
        "review_request_delay_minutes",
        "waitlist_notification_cooldown_minutes",
        "waitlist_default_expiration_days",
        "portfolio_max_media",
        "portfolio_page_size",
    )
    for column in columns:
        op.drop_column("business_settings", column)

    op.drop_column("appointments", "no_show_at")
    op.drop_column("appointments", "completed_at")
    op.drop_constraint("fk_users_self_booking_blocked_by_users", "users", type_="foreignkey")
    op.drop_column("users", "self_booking_block_reason")
    op.drop_column("users", "self_booking_blocked_by")
    op.drop_column("users", "self_booking_blocked_at")
    op.drop_column("users", "is_self_booking_blocked")
    op.drop_column("users", "repeat_booking_opt_out_at")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM notification_jobs
                WHERE notification_type::text IN ('review_request', 'repeat_booking_reminder')
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade: v0.2 notification jobs still exist';
            END IF;
        END $$
        """
    )
    op.execute("ALTER TYPE notification_type RENAME TO notification_type_v020")
    op.execute("CREATE TYPE notification_type AS ENUM ('client_reminder', 'admin_reminder')")
    op.execute(
        """
        ALTER TABLE notification_jobs
        ALTER COLUMN notification_type TYPE notification_type
        USING notification_type::text::notification_type
        """
    )
    op.execute("DROP TYPE notification_type_v020")

    bind = op.get_bind()
    consent_source.drop(bind, checkfirst=True)
    consent_type.drop(bind, checkfirst=True)
