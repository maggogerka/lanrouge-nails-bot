"""Restore a neutral owner name after the solo profile merge.

Revision ID: 20260812_0023
Revises: 20260812_0022
Create Date: 2026-08-12 06:45:00+03:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_0023"
down_revision: str | None = "20260812_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0022 could copy the historical placeholder name "Бизнес" onto a default
    # bootstrap owner. Restrict correction to that exact merge footprint.
    op.execute(
        """
        UPDATE staff_members owner_member
           SET display_name = 'Владелец'
         WHERE owner_member.is_bootstrap_owner
           AND owner_member.display_name = 'Бизнес'
           AND EXISTS (
               SELECT 1
                 FROM staff_members legacy
                WHERE legacy.business_id = owner_member.business_id
                  AND legacy.user_id IS NULL
                  AND legacy.display_name = 'Бизнес'
                  AND legacy.archived_at IS NOT NULL
           )
        """
    )


def downgrade() -> None:
    pass
