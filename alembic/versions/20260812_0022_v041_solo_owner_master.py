"""Unify the legacy solo master profile with the bootstrap owner.

Revision ID: 20260812_0022
Revises: 20260811_0021
Create Date: 2026-08-12 06:00:00+03:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_0022"
down_revision: str | None = "20260811_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A v0.3 master profile became unbound staff_member #1 in v0.4, while CLI
    # bootstrap created a second owner. In SOLO businesses that split is both
    # confusing and operationally wrong: services/windows belong to #1 but the
    # owner manages #2. Move all master-owned data to the immutable bootstrap
    # owner and preserve the richer public profile fields.
    op.execute(
        """
        DO $$
        DECLARE
            solo_business_id bigint;
            legacy_id bigint;
            owner_id bigint;
        BEGIN
            SELECT b.id, legacy.id, owner_member.id
              INTO solo_business_id, legacy_id, owner_id
              FROM businesses b
              JOIN staff_members legacy
                ON legacy.business_id = b.id
               AND legacy.user_id IS NULL
               AND legacy.is_bookable
               AND legacy.archived_at IS NULL
              JOIN staff_members owner_member
                ON owner_member.business_id = b.id
               AND owner_member.is_bootstrap_owner
               AND owner_member.user_id IS NOT NULL
               AND owner_member.archived_at IS NULL
             WHERE b.business_type = 'solo'
             ORDER BY legacy.id, owner_member.id
             LIMIT 1;

            IF legacy_id IS NULL OR owner_id IS NULL OR legacy_id = owner_id THEN
                RETURN;
            END IF;

            UPDATE staff_members owner_member
               SET display_name = CASE
                       WHEN owner_member.display_name IN ('Владелец', 'Бизнес')
                           THEN legacy.display_name
                       ELSE owner_member.display_name
                   END,
                   bio = COALESCE(owner_member.bio, legacy.bio),
                   specialization = COALESCE(owner_member.specialization, legacy.specialization),
                   telegram_photo_file_id = COALESCE(
                       owner_member.telegram_photo_file_id,
                       legacy.telegram_photo_file_id
                   ),
                   telegram_photo_file_unique_id = COALESCE(
                       owner_member.telegram_photo_file_unique_id,
                       legacy.telegram_photo_file_unique_id
                   ),
                   is_bookable = true
              FROM staff_members legacy
             WHERE owner_member.id = owner_id AND legacy.id = legacy_id;

            UPDATE staff_service_assignments target
               SET is_active = target.is_active OR source.is_active,
                   online_booking_enabled = (
                       target.online_booking_enabled OR source.online_booking_enabled
                   )
              FROM staff_service_assignments source
             WHERE target.business_id = solo_business_id
               AND target.staff_member_id = owner_id
               AND source.business_id = solo_business_id
               AND source.staff_member_id = legacy_id
               AND source.service_id = target.service_id
               AND source.archived_at IS NULL
               AND target.archived_at IS NULL;

            DELETE FROM staff_service_assignments source
             WHERE source.business_id = solo_business_id
               AND source.staff_member_id = legacy_id
               AND EXISTS (
                   SELECT 1 FROM staff_service_assignments target
                    WHERE target.business_id = solo_business_id
                      AND target.staff_member_id = owner_id
                      AND target.service_id = source.service_id
                      AND target.archived_at IS NULL
               );

            UPDATE staff_service_assignments SET staff_member_id = owner_id
             WHERE business_id = solo_business_id AND staff_member_id = legacy_id;
            UPDATE availability_windows SET staff_member_id = owner_id
             WHERE business_id = solo_business_id AND staff_member_id = legacy_id;
            UPDATE appointments SET staff_member_id = owner_id
             WHERE business_id = solo_business_id AND staff_member_id = legacy_id;
            UPDATE booking_reservations SET staff_member_id = owner_id
             WHERE business_id = solo_business_id AND staff_member_id = legacy_id;
            UPDATE portfolio_items SET staff_member_id = owner_id
             WHERE business_id = solo_business_id AND staff_member_id = legacy_id;
            UPDATE waitlist_entries SET preferred_staff_member_id = owner_id
             WHERE business_id = solo_business_id AND preferred_staff_member_id = legacy_id;
            UPDATE master_profiles SET staff_member_id = owner_id
             WHERE business_id = solo_business_id AND staff_member_id = legacy_id;
            UPDATE staff_weekly_intervals SET staff_member_id = owner_id
             WHERE business_id = solo_business_id AND staff_member_id = legacy_id;
            UPDATE staff_schedule_exceptions SET staff_member_id = owner_id
             WHERE business_id = solo_business_id AND staff_member_id = legacy_id;

            UPDATE staff_members
               SET is_active = false,
                   is_bookable = false,
                   role = 'master',
                   archived_at = now()
             WHERE id = legacy_id;
        END $$;
        """
    )


def downgrade() -> None:
    # The data ownership merge is intentionally irreversible. Recreating an
    # unbound duplicate would be misleading and could split future bookings.
    pass
