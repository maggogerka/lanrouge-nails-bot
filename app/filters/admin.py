"""Fresh DB-backed staff authorization for protected Telegram routers."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.domain.enums import StaffRole
from app.domain.errors import AuthorizationError
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.services.authorization_service import AuthorizationService


class IsStaff(BaseFilter):
    """Resolve active staff membership from the database for every update."""

    def __init__(
        self,
        *,
        allowed_roles: Collection[StaffRole],
        business_id: int = DEFAULT_BUSINESS_ID,
    ) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self._allowed_roles = frozenset(allowed_roles)
        self._business_id = business_id

    async def __call__(
        self,
        event: Message | CallbackQuery,
        authorization_service: AuthorizationService,
    ) -> bool | dict[str, Any]:
        sender = event.from_user
        if sender is None:
            return False
        try:
            context = await authorization_service.authorize(
                business_id=self._business_id,
                telegram_id=sender.id,
            )
        except AuthorizationError:
            return False
        if context.role not in self._allowed_roles:
            return False
        return {"staff_context": context}
