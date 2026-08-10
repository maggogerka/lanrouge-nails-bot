"""Scoped persistence for staff memberships and one-time invitations."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.business import Business, StaffInvitation, StaffMember
from app.database.models.user import User
from app.domain.enums import StaffInvitationStatus, StaffRole, UserRole
from app.schemas.authorization import StaffIdentity


class StaffRepository:
    """Keep business and active-membership predicates at the query boundary."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_business_for_update(self, business_id: int) -> Business | None:
        return (
            await self._session.scalars(
                select(Business).where(Business.id == business_id).with_for_update()
            )
        ).one_or_none()

    async def has_active_owner(self, business_id: int) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        StaffMember.business_id == business_id,
                        StaffMember.role == StaffRole.OWNER,
                        StaffMember.is_active.is_(True),
                        StaffMember.archived_at.is_(None),
                        StaffMember.user_id.is_not(None),
                    )
                )
            )
        )

    async def get_by_id(
        self,
        business_id: int,
        staff_member_id: int,
        *,
        for_update: bool = False,
    ) -> StaffMember | None:
        statement = select(StaffMember).where(
            StaffMember.id == staff_member_id,
            StaffMember.business_id == business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_user_id(
        self,
        business_id: int,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> StaffMember | None:
        statement = select(StaffMember).where(
            StaffMember.business_id == business_id,
            StaffMember.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_telegram_id(
        self,
        business_id: int,
        telegram_id: int,
        *,
        active_only: bool = True,
        for_update: bool = False,
    ) -> tuple[StaffMember, User] | None:
        statement = (
            select(StaffMember, User)
            .join(User, User.id == StaffMember.user_id)
            .where(
                StaffMember.business_id == business_id,
                User.telegram_id == telegram_id,
            )
        )
        if active_only:
            statement = statement.where(
                StaffMember.is_active.is_(True),
                StaffMember.archived_at.is_(None),
            )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    async def list_active_by_roles(
        self,
        business_id: int,
        roles: Collection[StaffRole],
    ) -> list[tuple[StaffMember, User]]:
        """Return bound, active staff identities scoped to one business."""

        if not roles:
            return []
        statement = (
            select(StaffMember, User)
            .join(User, User.id == StaffMember.user_id)
            .where(
                StaffMember.business_id == business_id,
                StaffMember.role.in_(roles),
                StaffMember.is_active.is_(True),
                StaffMember.archived_at.is_(None),
            )
            .order_by(StaffMember.sort_order, StaffMember.id)
        )
        rows = (await self._session.execute(statement)).all()
        return [(row[0], row[1]) for row in rows]

    async def list_members(self, business_id: int) -> list[StaffMember]:
        statement = (
            select(StaffMember)
            .where(StaffMember.business_id == business_id)
            .order_by(
                StaffMember.is_active.desc(),
                StaffMember.archived_at.asc().nulls_first(),
                StaffMember.sort_order,
                StaffMember.id,
            )
        )
        return list(await self._session.scalars(statement))

    async def list_active_invitations(
        self,
        business_id: int,
        *,
        now: datetime,
    ) -> list[StaffInvitation]:
        statement = (
            select(StaffInvitation)
            .where(
                StaffInvitation.business_id == business_id,
                StaffInvitation.status == StaffInvitationStatus.ACTIVE,
                StaffInvitation.expires_at > now,
            )
            .order_by(StaffInvitation.expires_at, StaffInvitation.id)
        )
        return list(await self._session.scalars(statement))

    async def add(self, member: StaffMember) -> StaffMember:
        self._session.add(member)
        await self._session.flush()
        return member

    async def get_or_create_user(self, identity: StaffIdentity) -> User:
        """Bind a numeric Telegram identity without granting authority in ``users.role``."""

        insert_statement = insert(User).values(
            telegram_id=identity.telegram_id,
            username=identity.username,
            first_name=identity.first_name,
            last_name=identity.last_name,
            role=UserRole.ADMIN,
        )
        updates: dict[str, object] = {"telegram_id": identity.telegram_id}
        if identity.username is not None:
            updates["username"] = identity.username
        if identity.first_name is not None:
            updates["first_name"] = identity.first_name
        if identity.last_name is not None:
            updates["last_name"] = identity.last_name
        statement = insert_statement.on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_=updates,
        ).returning(User)
        return (await self._session.scalars(statement)).one()

    async def add_invitation(self, invitation: StaffInvitation) -> StaffInvitation:
        self._session.add(invitation)
        await self._session.flush()
        return invitation

    async def get_invitation_by_digest(
        self,
        token_digest: str,
        *,
        for_update: bool = False,
    ) -> StaffInvitation | None:
        statement = select(StaffInvitation).where(StaffInvitation.token_digest == token_digest)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_invitation_by_id(
        self,
        business_id: int,
        invitation_id: int,
        *,
        for_update: bool = False,
    ) -> StaffInvitation | None:
        statement = select(StaffInvitation).where(
            StaffInvitation.id == invitation_id,
            StaffInvitation.business_id == business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def flush(self) -> None:
        await self._session.flush()
