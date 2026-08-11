"""Protect the immutable bootstrap owner and persist safe permission grants.

Revision ID: 20260811_0019
Revises: 20260811_0018
Create Date: 2026-08-11 15:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260811_0019"
down_revision: str | None = "20260811_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff_members",
        sa.Column(
            "is_bootstrap_owner",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "staff_members",
        sa.Column(
            "permission_grants",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_staff_members_bootstrap_owner_invariant"),
        "staff_members",
        "NOT is_bootstrap_owner OR "
        "(role = 'owner' AND is_active AND archived_at IS NULL AND user_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_staff_members_permission_grants_array"),
        "staff_members",
        "jsonb_typeof(permission_grants) = 'array' AND "
        "permission_grants <@ "
        '\'["invite_staff", "manage_staff", "manage_broadcasts", '
        '"override_booking_limit"]\'::jsonb',
    )
    op.create_index(
        "uq_staff_members_business_bootstrap_owner",
        "staff_members",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text("is_bootstrap_owner"),
    )
    op.execute(
        """
        CREATE FUNCTION protect_bootstrap_staff_member() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.is_bootstrap_owner THEN
                    RAISE EXCEPTION 'bootstrap owner cannot be deleted';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.is_bootstrap_owner AND (
                NEW.is_bootstrap_owner IS DISTINCT FROM TRUE
                OR NEW.business_id IS DISTINCT FROM OLD.business_id
                OR NEW.user_id IS DISTINCT FROM OLD.user_id
                OR NEW.role IS DISTINCT FROM 'owner'::staff_role
                OR NEW.is_active IS DISTINCT FROM TRUE
                OR NEW.archived_at IS NOT NULL
                OR NEW.permission_grants IS DISTINCT FROM OLD.permission_grants
            ) THEN
                RAISE EXCEPTION 'bootstrap owner is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_bootstrap_staff_member
        BEFORE UPDATE OR DELETE ON staff_members
        FOR EACH ROW EXECUTE FUNCTION protect_bootstrap_staff_member()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_bootstrap_user() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND EXISTS (
                SELECT 1 FROM staff_members
                WHERE user_id = OLD.id AND is_bootstrap_owner
            ) THEN
                RAISE EXCEPTION 'bootstrap owner user cannot be deleted';
            ELSIF TG_OP = 'DELETE' THEN
                RETURN OLD;
            ELSIF EXISTS (
                SELECT 1 FROM staff_members
                WHERE user_id = OLD.id AND is_bootstrap_owner
            ) AND (
                NEW.telegram_id IS DISTINCT FROM OLD.telegram_id
                OR (NEW.is_blocked IS TRUE AND OLD.is_blocked IS NOT TRUE)
            ) THEN
                RAISE EXCEPTION 'bootstrap owner user cannot be replaced or blocked';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_protect_bootstrap_user
        BEFORE UPDATE OR DELETE ON users
        FOR EACH ROW EXECUTE FUNCTION protect_bootstrap_user()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_protect_bootstrap_user ON users")
    op.execute("DROP FUNCTION IF EXISTS protect_bootstrap_user()")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_bootstrap_staff_member ON staff_members")
    op.execute("DROP FUNCTION IF EXISTS protect_bootstrap_staff_member()")
    op.drop_index("uq_staff_members_business_bootstrap_owner", table_name="staff_members")
    op.drop_constraint(
        op.f("ck_staff_members_permission_grants_array"),
        "staff_members",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_staff_members_bootstrap_owner_invariant"),
        "staff_members",
        type_="check",
    )
    op.drop_column("staff_members", "permission_grants")
    op.drop_column("staff_members", "is_bootstrap_owner")
