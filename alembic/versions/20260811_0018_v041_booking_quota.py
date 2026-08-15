"""Add configurable rolling client booking quota.

Revision ID: 20260811_0018
Revises: 20260811_0017
Create Date: 2026-08-11 14:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0018"
down_revision: str | None = "20260811_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "business_settings",
        sa.Column(
            "future_booking_limit_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "business_settings",
        sa.Column("future_booking_limit_max", sa.Integer(), server_default="4", nullable=False),
    )
    op.add_column(
        "business_settings",
        sa.Column(
            "future_booking_limit_horizon_days",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
    )
    op.add_column(
        "business_settings",
        sa.Column(
            "future_booking_count_client_cancellations",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_business_settings_future_booking_limit_max_valid"),
        "business_settings",
        "future_booking_limit_max BETWEEN 1 AND 100",
    )
    op.create_check_constraint(
        op.f("ck_business_settings_future_booking_limit_horizon_valid"),
        "business_settings",
        "future_booking_limit_horizon_days BETWEEN 1 AND 365",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_business_settings_future_booking_limit_horizon_valid"),
        "business_settings",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_business_settings_future_booking_limit_max_valid"),
        "business_settings",
        type_="check",
    )
    op.drop_column("business_settings", "future_booking_count_client_cancellations")
    op.drop_column("business_settings", "future_booking_limit_horizon_days")
    op.drop_column("business_settings", "future_booking_limit_max")
    op.drop_column("business_settings", "future_booking_limit_enabled")
