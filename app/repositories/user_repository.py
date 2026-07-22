"""SQLAlchemy persistence for Telegram users."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.domain.enums import UserRole
from app.schemas.service import AdminActor


class UserRepository:
    """Create or refresh the internal record for an authorized administrator."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_admin(self, actor: AdminActor) -> User:
        result = await self._session.execute(
            select(User).where(User.telegram_id == actor.telegram_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=actor.telegram_id,
                username=actor.username,
                first_name=actor.first_name,
                last_name=actor.last_name,
                role=UserRole.ADMIN,
            )
            self._session.add(user)
        else:
            user.username = actor.username
            user.first_name = actor.first_name
            user.last_name = actor.last_name
            user.role = UserRole.ADMIN
        await self._session.flush()
        return user
