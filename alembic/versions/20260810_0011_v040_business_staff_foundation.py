"""Add the v0.4 business, staff, invitation, client, and feature foundation.

Revision ID: 20260810_0011
Revises: 20260724_0010
Create Date: 2026-08-10 12:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0011"
down_revision: str | None = "20260724_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

business_type = postgresql.ENUM("solo", "salon", name="business_type", create_type=False)
business_status = postgresql.ENUM(
    "setup", "active", "suspended", "archived", name="business_status", create_type=False
)
staff_role = postgresql.ENUM(
    "owner", "manager", "master", "receptionist", name="staff_role", create_type=False
)
staff_invitation_status = postgresql.ENUM(
    "active", "used", "revoked", "expired", name="staff_invitation_status", create_type=False
)


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )


def upgrade() -> None:
    """Expand first, backfill the legacy singleton, and preserve every v0.3.1 row."""

    bind = op.get_bind()
    business_type.create(bind, checkfirst=True)
    business_status.create(bind, checkfirst=True)
    staff_role.create(bind, checkfirst=True)
    staff_invitation_status.create(bind, checkfirst=True)

    op.create_table(
        "businesses",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("legal_name", sa.String(length=500)),
        sa.Column("description", sa.Text()),
        sa.Column("short_description", sa.String(length=120)),
        sa.Column("business_type", business_type, server_default="solo", nullable=False),
        sa.Column("status", business_status, server_default="setup", nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("address", sa.String(length=500)),
        sa.Column("map_url", sa.String(length=2048)),
        sa.Column("contact_phone", sa.String(length=32)),
        sa.Column("contact_email", sa.String(length=320)),
        sa.Column("logo_telegram_file_id", sa.String(length=512)),
        sa.Column("logo_telegram_file_unique_id", sa.String(length=255)),
        sa.Column(
            "social_links",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("client_support_name", sa.String(length=100)),
        sa.Column("client_support_url", sa.String(length=2048)),
        sa.Column("client_support_hours", sa.String(length=255)),
        sa.Column("client_support_instructions", sa.Text()),
        sa.Column("privacy_policy_url", sa.String(length=2048)),
        sa.Column("privacy_policy_version", sa.String(length=64)),
        sa.Column("privacy_policy_hash", sa.String(length=64)),
        sa.Column("terms_url", sa.String(length=2048)),
        sa.Column("terms_version", sa.String(length=64)),
        sa.Column("terms_hash", sa.String(length=64)),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("setup_completed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'",
            name=op.f("ck_businesses_slug_format"),
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3", name=op.f("ck_businesses_currency_iso_length")
        ),
        sa.UniqueConstraint("slug", name="uq_businesses_slug"),
        sa.UniqueConstraint("instance_id", name="uq_businesses_instance_id"),
    )
    op.create_index("ix_businesses_status", "businesses", ["status"])
    op.execute(
        """
        INSERT INTO businesses (
            id, slug, display_name, business_type, status, timezone, currency,
            address, map_url, privacy_policy_url, instance_id, setup_completed_at
        )
        SELECT
            1, 'legacy-default', business_name, 'solo', 'active', timezone, 'RUB',
            NULLIF(address, ''), NULLIF(map_url, ''), NULL,
            'legacy-' || md5(current_database() || ':business:1'), now()
        FROM business_settings
        WHERE id = 1
        """
    )
    op.execute("SELECT setval(pg_get_serial_sequence('businesses', 'id'), 1, true)")

    op.create_table(
        "business_clients",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("anonymized_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_business_clients_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_business_clients_user_id_users")
        ),
    )
    op.create_index(
        "uq_business_clients_business_user",
        "business_clients",
        ["business_id", "user_id"],
        unique=True,
    )
    op.create_index(
        "ix_business_clients_business_created",
        "business_clients",
        ["business_id", "created_at"],
    )
    op.execute(
        "INSERT INTO business_clients (business_id, user_id) SELECT 1, id FROM users"
    )

    op.create_table(
        "staff_members",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger()),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("bio", sa.Text()),
        sa.Column("specialization", sa.String(length=500)),
        sa.Column("telegram_photo_file_id", sa.String(length=512)),
        sa.Column("telegram_photo_file_unique_id", sa.String(length=255)),
        sa.Column("role", staff_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_bookable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("schedule_paused_until", sa.DateTime(timezone=True)),
        sa.Column("max_daily_appointments", sa.Integer(), server_default="20", nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "sort_order BETWEEN -100000 AND 100000",
            name=op.f("ck_staff_members_sort_order_valid"),
        ),
        sa.CheckConstraint(
            "max_daily_appointments > 0",
            name=op.f("ck_staff_members_daily_limit_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_staff_members_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_staff_members_user_id_users")
        ),
    )
    op.create_index(
        "uq_staff_members_business_user",
        "staff_members",
        ["business_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_staff_members_business_active_role",
        "staff_members",
        ["business_id", "is_active", "role"],
    )
    op.create_index(
        "ix_staff_members_business_bookable_order",
        "staff_members",
        ["business_id", "is_bookable", "sort_order", "id"],
    )
    op.execute(
        """
        INSERT INTO staff_members (
            id, business_id, display_name, bio, telegram_photo_file_id,
            telegram_photo_file_unique_id, role, is_active, is_bookable
        )
        SELECT
            1, 1, display_name, bio, telegram_photo_file_id,
            telegram_photo_file_unique_id, 'owner', true, true
        FROM master_profiles
        WHERE id = 1
        """
    )
    op.execute("SELECT setval(pg_get_serial_sequence('staff_members', 'id'), 1, true)")

    op.create_table(
        "staff_invitations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("role", staff_role, nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_bookable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "status", staff_invitation_status, server_default="active", nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_staff_id", sa.BigInteger(), nullable=False),
        sa.Column("accepted_by_user_id", sa.BigInteger()),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by_staff_id", sa.BigInteger()),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(token_digest) = 64",
            name=op.f("ck_staff_invitations_token_digest_length"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_staff_invitations_expiry_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_staff_invitations_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_staff_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_staff_invitations_created_by_staff_id_staff_members")
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_staff_invitations_accepted_by_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_staff_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_staff_invitations_revoked_by_staff_id_staff_members")
        ),
        sa.UniqueConstraint("token_digest", name="uq_staff_invitations_token_digest"),
    )
    op.create_index(
        "ix_staff_invitations_business_status",
        "staff_invitations",
        ["business_id", "status", "expires_at"],
    )

    op.create_table(
        "business_feature_flags",
        sa.Column("business_id", sa.BigInteger(), primary_key=True),
        sa.Column("online_booking", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("master_selection", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("waitlist", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("portfolio", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reviews", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reference_photos", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("reminders", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("repeat_booking", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("broadcasts", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("loyalty", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("statistics", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("prepayment", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("manual_payments", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("yookassa_payments", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("mini_app", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("client_support", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_business_feature_flags_business_id_businesses")
        ),
    )
    op.execute(
        """
        INSERT INTO business_feature_flags (
            business_id, waitlist, portfolio, reviews, broadcasts
        )
        SELECT id, waitlist_enabled, portfolio_enabled, reviews_enabled, broadcasts_enabled
        FROM business_settings WHERE id = 1
        """
    )


def downgrade() -> None:
    """Remove only the additive foundation; legacy v0.3.1 tables remain untouched."""

    op.drop_table("business_feature_flags")
    op.drop_index("ix_staff_invitations_business_status", table_name="staff_invitations")
    op.drop_table("staff_invitations")
    op.drop_index("ix_staff_members_business_bookable_order", table_name="staff_members")
    op.drop_index("ix_staff_members_business_active_role", table_name="staff_members")
    op.drop_index("uq_staff_members_business_user", table_name="staff_members")
    op.drop_table("staff_members")
    op.drop_index("ix_business_clients_business_created", table_name="business_clients")
    op.drop_index("uq_business_clients_business_user", table_name="business_clients")
    op.drop_table("business_clients")
    op.drop_index("ix_businesses_status", table_name="businesses")
    op.drop_table("businesses")

    bind = op.get_bind()
    staff_invitation_status.drop(bind, checkfirst=True)
    staff_role.drop(bind, checkfirst=True)
    business_status.drop(bind, checkfirst=True)
    business_type.drop(bind, checkfirst=True)
