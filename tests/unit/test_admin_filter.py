"""Numeric Telegram ID authorization tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from aiogram.types import Message

from app.config import Settings
from app.filters.admin import IsAdmin


def make_settings(admin_ids: str) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        ADMIN_TELEGRAM_IDS=admin_ids,
    )


@pytest.mark.asyncio
async def test_admin_filter_uses_numeric_sender_id() -> None:
    event = cast(Message, SimpleNamespace(from_user=SimpleNamespace(id=101)))

    assert await IsAdmin()(event, make_settings("101"))
    assert not await IsAdmin()(event, make_settings("202"))


@pytest.mark.asyncio
async def test_admin_filter_rejects_missing_sender() -> None:
    event = cast(Message, SimpleNamespace(from_user=None))

    assert not await IsAdmin()(event, make_settings("101"))
