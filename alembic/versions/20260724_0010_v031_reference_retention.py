"""Add bounded lifecycle metadata for appointment references.

Revision ID: 20260724_0010
Revises: 20260723_0009
Create Date: 2026-07-24 02:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260724_0010"
down_revision: str | None = "20260723_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill expiry from the appointment lifecycle without deleting active data."""

    op.add_column(
        "appointment_reference_media",
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "appointment_reference_media",
        sa.Column("deletion_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "appointment_reference_media",
        sa.Column("last_deletion_error", sa.String(length=100)),
    )
    op.alter_column("appointment_reference_media", "telegram_file_id", nullable=True)
    op.alter_column("appointment_reference_media", "telegram_file_unique_id", nullable=True)
    op.execute(
        "UPDATE appointment_reference_media AS media SET expires_at = CASE "
        "WHEN appointments.status = 'completed' THEN "
        "COALESCE(appointments.completed_at, windows.end_at) + interval '30 days' "
        "WHEN appointments.status IN ('cancelled_by_client', 'cancelled_by_admin') THEN "
        "COALESCE(appointments.cancelled_at, windows.end_at) + interval '7 days' "
        "WHEN appointments.status = 'no_show' THEN "
        "windows.end_at + interval '14 days' "
        "ELSE windows.end_at + interval '30 days' END "
        "FROM appointments JOIN availability_windows AS windows "
        "ON windows.id = appointments.window_id "
        "WHERE appointments.id = media.appointment_id"
    )
    op.execute(
        "UPDATE appointment_reference_media SET telegram_file_id = NULL, "
        "telegram_file_unique_id = NULL, expires_at = COALESCE(expires_at, deleted_at) "
        "WHERE deleted_at IS NOT NULL"
    )
    op.alter_column("appointment_reference_media", "expires_at", nullable=False)
    op.create_check_constraint(
        "ck_appointment_reference_media_active_identifiers_present",
        "appointment_reference_media",
        "(deleted_at IS NULL AND telegram_file_id IS NOT NULL "
        "AND telegram_file_unique_id IS NOT NULL) OR deleted_at IS NOT NULL",
    )
    op.create_index(
        "ix_appointment_reference_expiry",
        "appointment_reference_media",
        ["expires_at", "id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "reference_cleanup_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.CheckConstraint("id = 1", name="ck_reference_cleanup_state_singleton"),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_reference_cleanup_state_failures_non_negative",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reference_cleanup_state"),
    )
    op.execute(
        "INSERT INTO reference_cleanup_state (id, consecutive_failures) VALUES (1, 0)"
    )


def downgrade() -> None:
    """Refuse downgrade after identifiers have been anonymized."""

    bind = op.get_bind()
    deleted = bind.execute(
        sa.text("SELECT count(*) FROM appointment_reference_media WHERE deleted_at IS NOT NULL")
    ).scalar_one()
    if deleted:
        raise RuntimeError(
            "Refusing downgrade: cleaned Telegram identifiers cannot be reconstructed"
        )
    op.drop_table("reference_cleanup_state")
    op.drop_index("ix_appointment_reference_expiry", table_name="appointment_reference_media")
    op.drop_constraint(
        "ck_appointment_reference_media_active_identifiers_present",
        "appointment_reference_media",
        type_="check",
    )
    op.alter_column("appointment_reference_media", "telegram_file_unique_id", nullable=False)
    op.alter_column("appointment_reference_media", "telegram_file_id", nullable=False)
    op.drop_column("appointment_reference_media", "last_deletion_error")
    op.drop_column("appointment_reference_media", "deletion_attempts")
    op.drop_column("appointment_reference_media", "expires_at")
