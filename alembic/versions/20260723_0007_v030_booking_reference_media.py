"""Add ordered Telegram reference photos for appointments.

Revision ID: 20260723_0007
Revises: 20260723_0006
Create Date: 2026-07-23 19:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260723_0007"
down_revision: str | None = "20260723_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

media_type = postgresql.ENUM("photo", name="media_type", create_type=False)


def upgrade() -> None:
    """Create reference rows without changing existing appointments."""

    op.create_table(
        "appointment_reference_media",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("appointment_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=512), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(length=255), nullable=False),
        sa.Column("media_type", media_type, server_default="photo", nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "position >= 0", name="ck_appointment_reference_media_position_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="fk_appointment_reference_media_appointment_id_appointments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name="fk_appointment_reference_media_uploaded_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointment_reference_media"),
        sa.UniqueConstraint(
            "appointment_id",
            "position",
            name="uq_appointment_reference_position",
        ),
        sa.UniqueConstraint(
            "appointment_id",
            "telegram_file_unique_id",
            name="uq_appointment_reference_file",
        ),
    )
    op.create_index(
        "ix_appointment_reference_active",
        "appointment_reference_media",
        ["appointment_id", "deleted_at"],
    )


def downgrade() -> None:
    """Drop only after exporting or confirming absence of reference rows."""

    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM appointment_reference_media")).scalar_one()
    if count:
        raise RuntimeError(
            "Refusing downgrade: appointment_reference_media contains production data"
        )
    op.drop_index("ix_appointment_reference_active", table_name="appointment_reference_media")
    op.drop_table("appointment_reference_media")
