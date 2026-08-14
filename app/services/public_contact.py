"""Safe public contact projection shared by booking workflows."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.business import StaffMember
from app.database.models.user import User
from app.schemas.public_links import public_links_from_mapping


async def resolve_staff_contact_url(
    session: AsyncSession,
    member: StaffMember | None,
) -> str | None:
    """Resolve the selected specialist's primary contact without legacy global fallback."""

    if member is None:
        return None
    links = public_links_from_mapping(
        member.settings.get("social_links") if member.settings else None
    )
    if links:
        return links[0].url
    if member.user_id is None:
        return None
    user = await session.get(User, member.user_id)
    if user is None:
        return None
    if user.username:
        return f"https://t.me/{user.username}"
    # Telegram can reject tg://user?id buttons for users with restricted
    # privacy, which makes the whole message fail. Without a public username
    # there is no universally valid profile URL.
    return None
