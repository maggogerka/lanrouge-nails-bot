"""Persistence for paginated client cards, CRM tags, notes and consent history."""

from __future__ import annotations

from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BusinessClient,
    ClientNote,
    ClientTag,
    ConsentHistory,
    User,
    UserClientTag,
)
from app.repositories.scoped import TenantScopedRepository


class CrmRepository(TenantScopedRepository):
    """Administrator-only CRM queries with stable pagination."""

    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def search_clients(
        self,
        *,
        query: str | None,
        tag_id: int | None,
        limit: int,
        offset: int,
    ) -> tuple[list[User], int]:
        filters = [
            BusinessClient.business_id == self.business_id,
            BusinessClient.is_active.is_(True),
            BusinessClient.anonymized_at.is_(None),
        ]
        if query:
            normalized = query.casefold().strip()
            pattern = f"%{normalized}%"
            filters.append(
                or_(
                    func.lower(func.coalesce(User.first_name, "")).like(pattern),
                    func.lower(func.coalesce(User.last_name, "")).like(pattern),
                    func.lower(func.coalesce(User.username, "")).like(pattern),
                    func.coalesce(User.phone, "").like(f"%{query.strip()}%"),
                    cast(User.id, String).like(f"%{query.strip()}%"),
                )
            )
        rows = select(User).join(BusinessClient, BusinessClient.user_id == User.id)
        count = select(func.count(User.id)).join(BusinessClient, BusinessClient.user_id == User.id)
        if tag_id is not None:
            rows = rows.join(UserClientTag, UserClientTag.user_id == User.id)
            count = count.join(UserClientTag, UserClientTag.user_id == User.id)
            filters.append(UserClientTag.tag_id == tag_id)
        rows = (
            rows.where(*filters)
            .order_by(func.lower(func.coalesce(User.first_name, "")), User.id)
            .limit(limit)
            .offset(offset)
        )
        count = count.where(*filters)
        return list((await self._session.scalars(rows)).all()), int(
            (await self._session.scalar(count)) or 0
        )

    async def get_tag(self, tag_id: int, *, for_update: bool = False) -> ClientTag | None:
        statement = select(ClientTag).where(
            ClientTag.id == tag_id,
            ClientTag.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def list_tags(self, *, active_only: bool = False) -> list[ClientTag]:
        statement = select(ClientTag).where(ClientTag.business_id == self.business_id)
        if active_only:
            statement = statement.where(ClientTag.is_active.is_(True))
        result = await self._session.scalars(
            statement.order_by(ClientTag.is_active.desc(), func.lower(ClientTag.name), ClientTag.id)
        )
        return list(result.all())

    async def list_client_tags(self, user_id: int) -> list[ClientTag]:
        result = await self._session.scalars(
            select(ClientTag)
            .join(UserClientTag, UserClientTag.tag_id == ClientTag.id)
            .where(
                UserClientTag.user_id == user_id,
                UserClientTag.business_id == self.business_id,
                ClientTag.business_id == self.business_id,
            )
            .order_by(func.lower(ClientTag.name), ClientTag.id)
        )
        return list(result.all())

    async def add_tag(self, tag: ClientTag) -> ClientTag:
        self._require_business(tag.business_id)
        self._session.add(tag)
        await self._session.flush()
        return tag

    async def assign_tag(self, *, user_id: int, tag_id: int, assigned_by: int) -> bool:
        client_exists = await self._session.scalar(
            select(BusinessClient.id).where(
                BusinessClient.business_id == self.business_id,
                BusinessClient.user_id == user_id,
                BusinessClient.is_active.is_(True),
            )
        )
        tag_exists = await self._session.scalar(
            select(ClientTag.id).where(
                ClientTag.business_id == self.business_id,
                ClientTag.id == tag_id,
            )
        )
        if client_exists is None or tag_exists is None:
            raise ValueError("client or tag belongs to another business")
        statement = (
            insert(UserClientTag)
            .values(
                business_id=self.business_id,
                user_id=user_id,
                tag_id=tag_id,
                assigned_by=assigned_by,
            )
            .on_conflict_do_nothing(index_elements=["business_id", "user_id", "tag_id"])
            .returning(UserClientTag.user_id)
        )
        return (await self._session.scalar(statement)) is not None

    async def remove_tag(self, *, user_id: int, tag_id: int) -> bool:
        result = await self._session.execute(
            delete(UserClientTag)
            .where(
                UserClientTag.business_id == self.business_id,
                UserClientTag.user_id == user_id,
                UserClientTag.tag_id == tag_id,
            )
            .returning(UserClientTag.user_id)
        )
        return result.scalar_one_or_none() is not None

    async def get_note(self, note_id: int, *, for_update: bool = False) -> ClientNote | None:
        statement = select(ClientNote).where(
            ClientNote.id == note_id,
            ClientNote.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def list_notes(
        self, client_id: int, *, include_archived: bool = False
    ) -> list[ClientNote]:
        statement = select(ClientNote).where(
            ClientNote.client_id == client_id,
            ClientNote.business_id == self.business_id,
        )
        if not include_archived:
            statement = statement.where(ClientNote.archived_at.is_(None))
        result = await self._session.scalars(
            statement.order_by(ClientNote.created_at.desc(), ClientNote.id.desc())
        )
        return list(result.all())

    async def add_note(self, note: ClientNote) -> ClientNote:
        self._require_business(note.business_id)
        self._session.add(note)
        await self._session.flush()
        return note

    async def add_consent_history(self, history: ConsentHistory) -> ConsentHistory:
        self._require_business(history.business_id)
        self._session.add(history)
        await self._session.flush()
        return history
