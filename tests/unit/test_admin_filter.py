"""Numeric Telegram ID authorization tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from aiogram.types import Message

from app.filters.admin import IsAdmin


@pytest.mark.asyncio
async def test_admin_filter_uses_numeric_sender_id() -> None:
    event = cast(Message, SimpleNamespace(from_user=SimpleNamespace(id=101)))

    assert await IsAdmin(frozenset({101}))(event)
    assert not await IsAdmin(frozenset({202}))(event)


@pytest.mark.asyncio
async def test_admin_filter_rejects_missing_sender() -> None:
    event = cast(Message, SimpleNamespace(from_user=None))

    assert not await IsAdmin(frozenset({101}))(event)
