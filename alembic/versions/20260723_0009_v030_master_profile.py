"""Add public master profile and ordered external links.

Revision ID: 20260723_0009
Revises: 20260723_0008
Create Date: 2026-07-23 21:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260723_0009"
down_revision: str | None = "20260723_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create an unpublished draft from existing public business fields."""

    op.create_table(
        "master_profiles",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("bio", sa.Text()),
        sa.Column("telegram_photo_file_id", sa.String(length=512)),
        sa.Column("telegram_photo_file_unique_id", sa.String(length=255)),
        sa.Column("address", sa.String(length=500)),
        sa.Column("map_url", sa.String(length=2048)),
        sa.Column("telegram_url", sa.String(length=2048)),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_master_profiles_singleton"),
        sa.CheckConstraint(
            "bio IS NULL OR char_length(bio) <= 4000",
            name="ck_master_profiles_bio_length_valid",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_master_profiles_updated_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_master_profiles"),
    )
    op.execute(
        "INSERT INTO master_profiles "
        "(id, display_name, address, map_url, telegram_url, is_published) "
        "SELECT 1, business_name, address, map_url, master_telegram_url, false "
        "FROM business_settings WHERE id = 1"
    )
    op.create_table(
        "master_public_links",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("profile_id", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN -100000 AND 100000",
            name="ck_master_public_links_sort_order_valid",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["master_profiles.id"],
            name="fk_master_public_links_profile_id_master_profiles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_master_public_links_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_master_public_links_updated_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_master_public_links"),
    )
    op.create_index(
        "ix_master_public_links_active_order",
        "master_public_links",
        ["is_active", "sort_order", "id"],
    )


def downgrade() -> None:
    """Refuse to discard customized profile data silently."""

    bind = op.get_bind()
    link_count = bind.execute(sa.text("SELECT count(*) FROM master_public_links")).scalar_one()
    profile = bind.execute(
        sa.text(
            "SELECT is_published, bio, telegram_photo_file_id FROM master_profiles WHERE id = 1"
        )
    ).one_or_none()
    if link_count or (profile and (profile[0] or profile[1] or profile[2])):
        raise RuntimeError("Refusing downgrade: master profile contains v0.3 content")
    op.drop_index("ix_master_public_links_active_order", table_name="master_public_links")
    op.drop_table("master_public_links")
    op.drop_table("master_profiles")
