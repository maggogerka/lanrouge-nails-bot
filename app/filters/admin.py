"""Admin authorization based only on numeric Telegram user IDs."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message


class IsAdmin(BaseFilter):
    """Allow an event only when its sender ID is configured as an admin."""

    def __init__(self, admin_telegram_ids: frozenset[int]) -> None:
        self._admin_telegram_ids = admin_telegram_ids

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and event.from_user.id in self._admin_telegram_ids
