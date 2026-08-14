"""Move service workstations from free windows to actual appointments.

Revision ID: 20260814_0027
Revises: 20260814_0026
Create Date: 2026-08-14 06:30:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0027"
down_revision: str | None = "20260814_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "business_settings",
        sa.Column("reschedule_deadline_hours", sa.Integer(), server_default="24", nullable=False),
    )
    op.create_check_constraint(
        "ck_business_settings_reschedule_deadline_positive",
        "business_settings",
        "reschedule_deadline_hours > 0",
    )

    op.add_column("appointments", sa.Column("workstation_id", sa.BigInteger()))
    op.create_foreign_key(
        op.f("fk_appointments_workstation_id_workstations"),
        "appointments",
        "workstations",
        ["workstation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        UPDATE appointments AS appointment
        SET workstation_id = availability.workstation_id
        FROM availability_windows AS availability
        WHERE appointment.window_id = availability.id
          AND appointment.business_id = availability.business_id
          AND availability.workstation_id IS NOT NULL
        """
    )
    op.create_index(
        "ix_appointments_business_workstation_start",
        "appointments",
        ["business_id", "workstation_id", "scheduled_start_at"],
    )
    op.execute(
        """
        ALTER TABLE appointments
        ADD CONSTRAINT ex_appointments_workstation_active_overlap
        EXCLUDE USING gist (
            workstation_id WITH =,
            tstzrange(scheduled_start_at, scheduled_end_at, '[)') WITH &&
        )
        WHERE (
            workstation_id IS NOT NULL
            AND status IN (
                'pending_payment', 'pending_manual_confirmation',
                'confirmed', 'client_confirmed'
            )
        )
        """
    )

    op.execute(
        "ALTER TABLE availability_windows DROP CONSTRAINT "
        "ex_availability_windows_workstation_active_overlap"
    )
    op.execute("UPDATE availability_windows SET service_id = NULL, workstation_id = NULL")


def downgrade() -> None:
    op.execute(
        """
        UPDATE availability_windows AS availability
        SET service_id = appointment.service_id,
            workstation_id = appointment.workstation_id
        FROM appointments AS appointment
        WHERE appointment.window_id = availability.id
          AND appointment.business_id = availability.business_id
          AND appointment.workstation_id IS NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE availability_windows
        ADD CONSTRAINT ex_availability_windows_workstation_active_overlap
        EXCLUDE USING gist (
            workstation_id WITH =,
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (
            workstation_id IS NOT NULL
            AND status IN ('open', 'reserved', 'booked')
        )
        """
    )

    op.execute(
        "ALTER TABLE appointments DROP CONSTRAINT ex_appointments_workstation_active_overlap"
    )
    op.drop_index("ix_appointments_business_workstation_start", table_name="appointments")
    op.drop_constraint(
        op.f("fk_appointments_workstation_id_workstations"),
        "appointments",
        type_="foreignkey",
    )
    op.drop_column("appointments", "workstation_id")

    op.drop_constraint(
        "ck_business_settings_reschedule_deadline_positive",
        "business_settings",
        type_="check",
    )
    op.drop_column("business_settings", "reschedule_deadline_hours")
