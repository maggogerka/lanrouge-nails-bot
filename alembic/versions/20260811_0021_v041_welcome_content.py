"""Add draft and published white-label welcome content.

Revision ID: 20260811_0021
Revises: 20260811_0020
Create Date: 2026-08-11 19:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0021"
down_revision: str | None = "20260811_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Replace only the exact historical demo identity. Customized production
    # values are deliberately preserved.
    op.execute(
        "UPDATE business_settings SET business_name = 'Бизнес' "
        "WHERE lower(btrim(business_name)) = 'lanrouge nails'"
    )
    op.execute(
        "UPDATE business_settings SET master_telegram_url = 'https://t.me/example_service_bot' "
        "WHERE lower(btrim(master_telegram_url)) = 'https://t.me/lanrouge'"
    )
    op.execute(
        "UPDATE businesses SET display_name = 'Бизнес' "
        "WHERE lower(btrim(display_name)) = 'lanrouge nails'"
    )
    op.execute(
        "UPDATE master_profiles SET display_name = 'Специалист' "
        "WHERE lower(btrim(display_name)) = 'lanrouge nails'"
    )
    op.execute(
        "UPDATE master_profiles SET telegram_url = 'https://t.me/example_service_bot' "
        "WHERE lower(btrim(telegram_url)) = 'https://t.me/lanrouge'"
    )
    op.execute(
        "UPDATE staff_members SET display_name = 'Специалист' "
        "WHERE lower(btrim(display_name)) = 'lanrouge nails'"
    )
    op.execute(
        "UPDATE appointments SET master_name_snapshot = 'Специалист' "
        "WHERE lower(btrim(master_name_snapshot)) = 'lanrouge nails'"
    )
    for name, type_ in (
        ("welcome_draft_text", sa.Text()),
        ("welcome_draft_photo_file_id", sa.String(length=512)),
        ("welcome_draft_photo_unique_id", sa.String(length=255)),
        ("welcome_published_text", sa.Text()),
        ("welcome_published_photo_file_id", sa.String(length=512)),
        ("welcome_published_photo_unique_id", sa.String(length=255)),
        ("welcome_published_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("businesses", sa.Column(name, type_))
    op.create_check_constraint(
        op.f("ck_businesses_welcome_text_length_valid"),
        "businesses",
        "(welcome_draft_text IS NULL OR char_length(welcome_draft_text) <= 7000) AND "
        "(welcome_published_text IS NULL OR char_length(welcome_published_text) <= 7000)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_businesses_welcome_text_length_valid"), "businesses", type_="check")
    for name in (
        "welcome_published_at",
        "welcome_published_photo_unique_id",
        "welcome_published_photo_file_id",
        "welcome_published_text",
        "welcome_draft_photo_unique_id",
        "welcome_draft_photo_file_id",
        "welcome_draft_text",
    ):
        op.drop_column("businesses", name)
