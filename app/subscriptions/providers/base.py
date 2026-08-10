"""External CRM billing status boundary, separate from client payments."""

from __future__ import annotations

from typing import Protocol

from app.schemas.subscription import SubscriptionView


class SubscriptionStatusProvider(Protocol):
    """Return a safe status projection without exposing billing credentials."""

    async def get_status(self, business_id: int) -> SubscriptionView: ...
