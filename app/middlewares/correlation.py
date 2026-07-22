"""Correlation IDs for one Telegram update processing chain."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.logging import reset_correlation_id, set_correlation_id

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class CorrelationIdMiddleware(BaseMiddleware):
    """Attach a random, non-PII request ID to logs and handler data."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        correlation_id = uuid4().hex
        token = set_correlation_id(correlation_id)
        data["correlation_id"] = correlation_id
        try:
            return await handler(event, data)
        finally:
            reset_correlation_id(token)
