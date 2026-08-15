"""Add per-service additions and immutable appointment snapshots.

Revision ID: 20260811_0017
Revises: 20260811_0016
Create Date: 2026-08-11 13:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0017"
down_revision: str | None = "20260811_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_addons",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("duration_min_minutes", sa.Integer(), nullable=False),
        sa.Column("duration_max_minutes", sa.Integer(), nullable=False),
        sa.Column("telegram_photo_file_id", sa.String(length=512)),
        sa.Column("telegram_photo_file_unique_id", sa.String(length=255)),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_service_addons_price_non_negative")),
        sa.CheckConstraint(
            "duration_min_minutes > 0",
            name=op.f("ck_service_addons_duration_min_positive"),
        ),
        sa.CheckConstraint(
            "duration_max_minutes > 0",
            name=op.f("ck_service_addons_duration_max_positive"),
        ),
        sa.CheckConstraint(
            "duration_min_minutes <= duration_max_minutes",
            name=op.f("ck_service_addons_duration_range_valid"),
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_service_addons_business_service_order",
        "service_addons",
        ["business_id", "service_id", "is_active", "sort_order"],
    )
    op.create_table(
        "appointment_addon_snapshots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("appointment_id", sa.BigInteger(), nullable=False),
        sa.Column("service_addon_id", sa.BigInteger(), nullable=False),
        sa.Column("name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("description_snapshot", sa.Text()),
        sa.Column("price_snapshot", sa.Numeric(12, 2), nullable=False),
        sa.Column("duration_min_snapshot", sa.Integer(), nullable=False),
        sa.Column("duration_max_snapshot", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "price_snapshot >= 0",
            name=op.f("ck_appointment_addon_snapshots_price_snapshot_non_negative"),
        ),
        sa.CheckConstraint(
            "duration_min_snapshot > 0",
            name=op.f("ck_appointment_addon_snapshots_duration_min_snapshot_positive"),
        ),
        sa.CheckConstraint(
            "duration_max_snapshot > 0",
            name=op.f("ck_appointment_addon_snapshots_duration_max_snapshot_positive"),
        ),
        sa.CheckConstraint(
            "duration_min_snapshot <= duration_max_snapshot",
            name=op.f("ck_appointment_addon_snapshots_duration_snapshot_range_valid"),
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_addon_id"], ["service_addons.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_appointment_addon_snapshots_appointment_addon",
        "appointment_addon_snapshots",
        ["appointment_id", "service_addon_id"],
        unique=True,
    )
    op.create_index(
        "ix_appointment_addon_snapshots_business_appointment",
        "appointment_addon_snapshots",
        ["business_id", "appointment_id", "position"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointment_addon_snapshots_business_appointment",
        table_name="appointment_addon_snapshots",
    )
    op.drop_index(
        "uq_appointment_addon_snapshots_appointment_addon",
        table_name="appointment_addon_snapshots",
    )
    op.drop_table("appointment_addon_snapshots")
    op.drop_index("ix_service_addons_business_service_order", table_name="service_addons")
    op.drop_table("service_addons")
