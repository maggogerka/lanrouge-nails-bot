"""Store business location and selected-master contact on appointments.

Revision ID: 20260814_0024
Revises: 20260812_0023
Create Date: 2026-08-14 12:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0024"
down_revision: str | None = "20260812_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("address_snapshot", sa.String(500)))
    op.add_column("appointments", sa.Column("map_url_snapshot", sa.String(2048)))
    op.add_column("appointments", sa.Column("master_contact_url_snapshot", sa.String(2048)))
    op.execute(
        """
        UPDATE appointments appointment
           SET address_snapshot = business.address,
               map_url_snapshot = business.map_url
          FROM businesses business
         WHERE business.id = appointment.business_id
        """
    )


def downgrade() -> None:
    op.drop_column("appointments", "master_contact_url_snapshot")
    op.drop_column("appointments", "map_url_snapshot")
    op.drop_column("appointments", "address_snapshot")
