"""Mandatory tenant context shared by business-data repositories."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.tenancy import DEFAULT_BUSINESS_ID


class TenantScopedRepository:
    """Base repository that never represents an unscoped business query."""

    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self._session = session
        self.business_id = business_id

    def _require_business(self, business_id: int) -> None:
        """Reject an entity assembled for another tenant before it reaches SQL."""

        if business_id != self.business_id:
            raise ValueError("entity belongs to another business")
