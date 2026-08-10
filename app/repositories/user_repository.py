"""SQLAlchemy persistence for Telegram users."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BusinessClient, User
from app.domain.enums import UserRole
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository
from app.schemas.booking import ClientActor
from app.schemas.service import AdminActor


class UserRepository(TenantScopedRepository):
    """Create or refresh the internal record for an authorized administrator."""

    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def get_by_telegram_id(
        self,
        telegram_id: int,
        *,
        for_update: bool = False,
    ) -> User | None:
        statement = (
            select(User)
            .join(BusinessClient, BusinessClient.user_id == User.id)
            .where(
                User.telegram_id == telegram_id,
                BusinessClient.business_id == self.business_id,
                BusinessClient.is_active.is_(True),
                BusinessClient.anonymized_at.is_(None),
            )
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def get_by_id(self, user_id: int, *, for_update: bool = False) -> User | None:
        statement = (
            select(User)
            .join(BusinessClient, BusinessClient.user_id == User.id)
            .where(
                User.id == user_id,
                BusinessClient.business_id == self.business_id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def get_or_create_client(self, actor: ClientActor) -> User:
        """Upsert a Telegram identity without overwriting authority or a booking name."""

        statement = (
            insert(User)
            .values(
                telegram_id=actor.telegram_id,
                username=actor.username,
                first_name=actor.first_name,
                last_name=actor.last_name,
                role=UserRole.CLIENT,
            )
            .on_conflict_do_update(
                index_elements=[User.telegram_id],
                set_={
                    "username": actor.username,
                    "last_name": actor.last_name,
                },
            )
            .returning(User)
        )
        result = await self._session.scalars(statement)
        user = result.one()
        await self._ensure_business_client(user.id)
        return user

    async def list_by_telegram_ids(self, telegram_ids: frozenset[int]) -> list[User]:
        if not telegram_ids:
            return []
        result = await self._session.scalars(
            select(User)
            .join(BusinessClient, BusinessClient.user_id == User.id)
            .where(
                User.telegram_id.in_(telegram_ids),
                BusinessClient.business_id == self.business_id,
                BusinessClient.is_active.is_(True),
                BusinessClient.anonymized_at.is_(None),
            )
        )
        return list(result.all())

    async def update_booking_profile(self, user: User, *, name: str, phone: str) -> None:
        user.first_name = name
        user.phone = phone
        await self._session.flush()

    async def set_privacy_consent(self, user: User, accepted_at: datetime) -> None:
        user.privacy_consent_at = accepted_at
        await self._session.flush()

    async def set_marketing_consent(
        self,
        user: User,
        *,
        accepted: bool,
        changed_at: datetime,
    ) -> None:
        user.marketing_consent_at = changed_at if accepted else None
        user.marketing_unsubscribed_at = None if accepted else changed_at
        await self._session.flush()

    async def mark_blocked(self, user: User) -> None:
        user.is_blocked = True
        await self._session.flush()

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
        await self._ensure_business_client(user.id)
        return user

    async def _ensure_business_client(self, user_id: int) -> None:
        await self._session.execute(
            insert(BusinessClient)
            .values(business_id=self.business_id, user_id=user_id)
            .on_conflict_do_nothing(index_elements=["business_id", "user_id"])
        )
