"""Fine-grained role permission guard for staff child routers."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.schemas.authorization import StaffContext, StaffPermission


class HasStaffPermission(BaseFilter):
    def __init__(self, permission: StaffPermission) -> None:
        self._permission = permission

    async def __call__(
        self,
        event: Message | CallbackQuery,
        staff_context: StaffContext,
    ) -> bool:
        del event
        return staff_context.has_permission(self._permission)
