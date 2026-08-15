"""Tenant-scoped CRM subscription persistence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.commerce import BusinessSubscription
from app.repositories.scoped import TenantScopedRepository


class SubscriptionRepository(TenantScopedRepository):
    """Read the billing state for exactly one business."""

    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def get(self, *, for_update: bool = False) -> BusinessSubscription | None:
        statement = select(BusinessSubscription).where(
            BusinessSubscription.business_id == self.business_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()
