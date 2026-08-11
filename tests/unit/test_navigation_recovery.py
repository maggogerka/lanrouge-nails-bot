"""Regression coverage for top-level navigation out of stale FSM drafts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.admin.appointment_browse import show_today
from app.handlers.common.commands import choose_staff_interface
from app.keyboards.admin.main import ADMIN_TODAY_TEXT
from app.keyboards.common.interface_mode import (
    InterfaceModeCallback,
    interface_mode_keyboard,
    return_to_management_keyboard,
)
from app.middlewares.navigation import GlobalNavigationMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/start", "/admin@my_bot", ADMIN_TODAY_TEXT])
async def test_top_level_navigation_clears_stale_state_before_dispatch(text: str) -> None:
    middleware = GlobalNavigationMiddleware()
    event = MagicMock(spec=Message)
    event.text = text
    state = MagicMock(spec=FSMContext)
    state.clear = AsyncMock()
    handler = AsyncMock(return_value="handled")

    result = await middleware(handler, event, {"state": state})

    assert result == "handled"
    state.clear.assert_awaited_once_with()
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_regular_form_input_keeps_current_state() -> None:
    event = MagicMock(spec=Message)
    event.text = "12:30"
    state = MagicMock(spec=FSMContext)
    state.clear = AsyncMock()
    handler = AsyncMock()

    await GlobalNavigationMiddleware()(handler, event, {"state": state})

    state.clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_today_empty_state_is_clear_and_not_an_error() -> None:
    message = MagicMock(spec=Message)
    message.from_user = SimpleNamespace(
        id=101,
        username="owner",
        first_name="Owner",
        last_name=None,
    )
    message.answer = AsyncMock()
    service = MagicMock()
    service.list_admin_today = AsyncMock(return_value=[])

    await show_today(message, service)

    service.list_admin_today.assert_awaited_once()
    assert message.answer.await_args.args[0] == "Записей на сегодня нет."


@pytest.mark.asyncio
async def test_staff_start_offers_explicit_interface_choice() -> None:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    state = MagicMock(spec=FSMContext)
    state.clear = AsyncMock()

    await choose_staff_interface(message, state)

    state.clear.assert_awaited_once_with()
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    actions = {
        InterfaceModeCallback.unpack(button.callback_data).action
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert actions == {"client", "management"}


def test_client_view_always_has_return_to_management_action() -> None:
    assert interface_mode_keyboard().inline_keyboard
    button = return_to_management_keyboard().inline_keyboard[0][0]

    assert InterfaceModeCallback.unpack(button.callback_data).action == "management"
