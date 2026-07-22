"""Add portfolio works, Telegram media, tags and booking design references.

Revision ID: 20260722_0003
Revises: 20260722_0002
Create Date: 2026-07-22 21:45:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260722_0003"
down_revision: str | None = "20260722_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

portfolio_status = postgresql.ENUM(
    "draft", "published", "archived", name="portfolio_status", create_type=False
)
media_type = postgresql.ENUM("photo", name="media_type", create_type=False)


def upgrade() -> None:
    """Create portfolio records without copying or downloading Telegram media."""

    bind = op.get_bind()
    portfolio_status.create(bind, checkfirst=True)
    media_type.create(bind, checkfirst=True)

    op.create_table(
        "portfolio_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("linked_service_id", sa.BigInteger()),
        sa.Column("design_price", sa.Numeric(precision=12, scale=2)),
        sa.Column("status", portfolio_status, server_default="draft", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
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
            "design_price IS NULL OR design_price >= 0",
            name="ck_portfolio_items_design_price_valid",
        ),
        sa.ForeignKeyConstraint(
            ["linked_service_id"],
            ["services.id"],
            name="fk_portfolio_items_linked_service_id_services",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_portfolio_items_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_portfolio_items"),
    )
    op.create_index(
        "ix_portfolio_items_status_order",
        "portfolio_items",
        ["status", "sort_order", "published_at"],
    )

    op.create_table(
        "portfolio_media",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("portfolio_item_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=512), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(length=255), nullable=False),
        sa.Column("media_type", media_type, server_default="photo", nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("position >= 0", name="ck_portfolio_media_position_non_negative"),
        sa.ForeignKeyConstraint(
            ["portfolio_item_id"],
            ["portfolio_items.id"],
            name="fk_portfolio_media_portfolio_item_id_portfolio_items",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_portfolio_media"),
        sa.UniqueConstraint(
            "portfolio_item_id", "position", name="uq_portfolio_media_position"
        ),
        sa.UniqueConstraint(
            "portfolio_item_id",
            "telegram_file_unique_id",
            name="uq_portfolio_media_file",
        ),
    )

    op.create_table(
        "portfolio_tags",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_portfolio_tags"),
        sa.UniqueConstraint("slug", name="uq_portfolio_tags_slug"),
    )
    op.create_index(
        "uq_portfolio_tags_name_ci", "portfolio_tags", [sa.text("lower(name)")], unique=True
    )

    op.create_table(
        "portfolio_item_tags",
        sa.Column("portfolio_item_id", sa.BigInteger(), nullable=False),
        sa.Column("tag_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["portfolio_item_id"],
            ["portfolio_items.id"],
            name="fk_portfolio_item_tags_portfolio_item_id_portfolio_items",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["portfolio_tags.id"],
            name="fk_portfolio_item_tags_tag_id_portfolio_tags",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("portfolio_item_id", "tag_id", name="pk_portfolio_item_tags"),
    )

    op.add_column("appointments", sa.Column("design_reference_id", sa.BigInteger()))
    op.add_column("appointments", sa.Column("design_title_snapshot", sa.String(length=255)))
    op.create_foreign_key(
        "fk_appointments_design_reference_id_portfolio_items",
        "appointments",
        "portfolio_items",
        ["design_reference_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_appointments_design_reference", "appointments", ["design_reference_id"]
    )


def downgrade() -> None:
    """Remove portfolio only after later feature revisions have been downgraded."""

    op.drop_index("ix_appointments_design_reference", table_name="appointments")
    op.drop_constraint(
        "fk_appointments_design_reference_id_portfolio_items",
        "appointments",
        type_="foreignkey",
    )
    op.drop_column("appointments", "design_title_snapshot")
    op.drop_column("appointments", "design_reference_id")
    op.drop_table("portfolio_item_tags")
    op.drop_index("uq_portfolio_tags_name_ci", table_name="portfolio_tags")
    op.drop_table("portfolio_tags")
    op.drop_table("portfolio_media")
    op.drop_index("ix_portfolio_items_status_order", table_name="portfolio_items")
    op.drop_table("portfolio_items")

    bind = op.get_bind()
    media_type.drop(bind, checkfirst=True)
    portfolio_status.drop(bind, checkfirst=True)
