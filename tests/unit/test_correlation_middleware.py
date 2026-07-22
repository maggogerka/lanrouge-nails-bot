"""Correlation middleware lifecycle test."""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.types import TelegramObject

from app.logging import get_correlation_id
from app.middlewares.correlation import CorrelationIdMiddleware


@pytest.mark.asyncio
async def test_correlation_is_available_only_during_handler() -> None:
    seen: list[str | None] = []
    data: dict[str, Any] = {}

    async def handler(event: TelegramObject, handler_data: dict[str, Any]) -> str:
        del event
        seen.append(get_correlation_id())
        assert handler_data["correlation_id"] == seen[0]
        return "ok"

    result = await CorrelationIdMiddleware()(handler, TelegramObject(), data)

    assert result == "ok"
    assert seen[0] is not None
    assert len(seen[0]) == 32
    assert get_correlation_id() is None
