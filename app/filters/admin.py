"""Admin authorization based only on numeric Telegram user IDs."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.config import Settings


class IsAdmin(BaseFilter):
    """Allow an event only when its sender ID is configured as an admin."""

    async def __call__(self, event: Message | CallbackQuery, settings: Settings) -> bool:
        return event.from_user is not None and event.from_user.id in settings.admin_telegram_ids
