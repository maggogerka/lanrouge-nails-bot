"""Add typed v0.3 UX, reference-media and portfolio settings.

Revision ID: 20260723_0006
Revises: 20260722_0005
Create Date: 2026-07-23 18:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260723_0006"
down_revision: str | None = "20260722_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

portfolio_display_mode = postgresql.ENUM(
    "internal",
    "external_link",
    "disabled",
    name="portfolio_display_mode",
    create_type=False,
)


def upgrade() -> None:
    """Add default-safe columns and preserve the legacy portfolio flag."""

    bind = op.get_bind()
    portfolio_display_mode.create(bind, checkfirst=True)
    columns = (
        sa.Column(
            "availability_date_picker_days", sa.Integer(), server_default="31", nullable=False
        ),
        sa.Column(
            "availability_time_step_minutes", sa.Integer(), server_default="60", nullable=False
        ),
        sa.Column(
            "booking_reference_max_media", sa.Integer(), server_default="10", nullable=False
        ),
        sa.Column(
            "booking_reference_edit_deadline_hours",
            sa.Integer(),
            server_default="36",
            nullable=False,
        ),
        sa.Column("booking_reference_retention_days", sa.Integer()),
        sa.Column(
            "portfolio_mode",
            portfolio_display_mode,
            server_default="internal",
            nullable=False,
        ),
        sa.Column("external_portfolio_url", sa.String(length=2048)),
        sa.Column(
            "external_portfolio_button_text",
            sa.String(length=100),
            server_default="Открыть портфолио",
            nullable=False,
        ),
        sa.Column(
            "master_profile_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    for column in columns:
        op.add_column("business_settings", column)

    op.execute(
        "UPDATE business_settings SET portfolio_mode = "
        "CASE WHEN portfolio_enabled THEN 'internal'::portfolio_display_mode "
        "ELSE 'disabled'::portfolio_display_mode END"
    )
    checks = (
        (
            "ck_business_settings_availability_date_picker_days_valid",
            "availability_date_picker_days BETWEEN 1 AND 62",
        ),
        (
            "ck_business_settings_availability_time_step_valid",
            "availability_time_step_minutes BETWEEN 1 AND 1440 "
            "AND MOD(1440, availability_time_step_minutes) = 0",
        ),
        (
            "ck_business_settings_booking_reference_max_media_valid",
            "booking_reference_max_media BETWEEN 1 AND 10",
        ),
        (
            "ck_business_settings_booking_reference_edit_deadline_valid",
            "booking_reference_edit_deadline_hours BETWEEN 1 AND 720",
        ),
        (
            "ck_business_settings_booking_reference_retention_valid",
            "booking_reference_retention_days IS NULL "
            "OR booking_reference_retention_days BETWEEN 1 AND 3650",
        ),
    )
    for name, condition in checks:
        op.create_check_constraint(name, "business_settings", condition)


def downgrade() -> None:
    """Remove additive settings without touching legacy portfolio data."""

    for name in (
        "ck_business_settings_booking_reference_retention_valid",
        "ck_business_settings_booking_reference_edit_deadline_valid",
        "ck_business_settings_booking_reference_max_media_valid",
        "ck_business_settings_availability_time_step_valid",
        "ck_business_settings_availability_date_picker_days_valid",
    ):
        op.drop_constraint(name, "business_settings", type_="check")
    for column in (
        "master_profile_enabled",
        "external_portfolio_button_text",
        "external_portfolio_url",
        "portfolio_mode",
        "booking_reference_retention_days",
        "booking_reference_edit_deadline_hours",
        "booking_reference_max_media",
        "availability_time_step_minutes",
        "availability_date_picker_days",
    ):
        op.drop_column("business_settings", column)
    portfolio_display_mode.drop(op.get_bind(), checkfirst=True)
