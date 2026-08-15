"""Move legacy support and bound-master contacts into the v0.4.1 model.

Revision ID: 20260814_0025
Revises: 20260814_0024
Create Date: 2026-08-14 13:00:00+03:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260814_0025"
down_revision: str | None = "20260814_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Preserve the old single support button in the new editable collection.  Only
    # migrate an empty collection so pre-existing multi-link configuration wins.
    op.execute(
        """
        UPDATE businesses
           SET social_links = jsonb_build_object(
                   left(
                       COALESCE(NULLIF(btrim(client_support_name), ''), 'Поддержка'),
                       64
                   ),
                   client_support_url
               ),
               client_support_name = NULL,
               client_support_url = NULL
         WHERE client_support_url IS NOT NULL
           AND btrim(client_support_url) <> ''
           AND social_links = '{}'::jsonb
        """
    )

    # Historical appointments did not snapshot the selected master's contact.
    # A bound Telegram identity is the safest tenant-scoped fallback.
    op.execute(
        """
        UPDATE appointments appointment
           SET master_contact_url_snapshot = CASE
                   WHEN app_user.username IS NOT NULL
                    AND btrim(app_user.username) <> ''
                       THEN 'https://t.me/' || app_user.username
                   ELSE 'tg://user?id=' || app_user.telegram_id::text
               END
          FROM staff_members staff
          JOIN users app_user ON app_user.id = staff.user_id
         WHERE appointment.staff_member_id = staff.id
           AND appointment.master_contact_url_snapshot IS NULL
        """
    )


def downgrade() -> None:
    # This is a lossless forward data migration. Reconstructing which public link
    # originally came from the deprecated singleton fields would be ambiguous.
    pass
