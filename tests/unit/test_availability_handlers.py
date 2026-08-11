"""Pure window handler formatting and callback tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import AvailabilityWindowStatus
from app.handlers.admin.window_common import parse_local_date, parse_local_time, render_window
from app.handlers.admin.window_create import begin_window_creation_from_callback
from app.keyboards.admin.windows import (
    WindowCallback,
    window_details_keyboard,
    window_list_keyboard,
)
from app.schemas.availability import AvailabilityWindowView


def window_view(
    status: AvailabilityWindowStatus = AvailabilityWindowStatus.OPEN,
) -> AvailabilityWindowView:
    return AvailabilityWindowView(
        id=42,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        status=status,
        admin_comment="Взять <палитру> & лампу",
        timezone="Europe/Moscow",
    )


def test_window_render_converts_timezone_and_escapes_comment() -> None:
    rendered = render_window(window_view())

    assert "23.07.2026" in rendered
    assert "10:00–13:30" in rendered
    assert "&lt;палитру&gt; &amp; лампу" in rendered


def test_window_date_and_time_parsers_are_strict() -> None:
    assert parse_local_date("23.07.2026").isoformat() == "2026-07-23"  # type: ignore[union-attr]
    assert parse_local_date("2026-07-23") is None
    assert parse_local_time("09:05").isoformat() == "09:05:00"  # type: ignore[union-attr]
    assert parse_local_time("25:00") is None


def test_window_callbacks_fit_telegram_limit() -> None:
    callback = WindowCallback(
        action="delete_confirm",
        window_id=9_223_372_036_854_775_807,
    ).pack()

    assert len(callback.encode()) <= 64
    assert window_details_keyboard(window_view()).inline_keyboard


def test_window_list_can_show_and_hide_archived_rows() -> None:
    visible = window_list_keyboard([window_view()], include_archived=False)
    archived = window_list_keyboard(
        [window_view(AvailabilityWindowStatus.CLOSED)],
        include_archived=True,
    )

    assert any("Показать архив" in button.text for row in visible.inline_keyboard for button in row)
    assert any("Скрыть архив" in button.text for row in archived.inline_keyboard for button in row)


def test_booked_window_still_offers_explicit_delete_flow() -> None:
    keyboard = window_details_keyboard(window_view(AvailabilityWindowStatus.BOOKED))
    actions = {
        WindowCallback.unpack(button.callback_data or "").action
        for row in keyboard.inline_keyboard
        for button in row
    }

    assert "delete_prompt" in actions


@pytest.mark.asyncio
async def test_add_window_callback_authorizes_clicking_admin_not_bot_message_author() -> None:
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = SimpleNamespace(
        id=101,
        username="owner",
        first_name="Owner",
        last_name=None,
    )
    callback.message = MagicMock(spec=Message)
    callback.message.from_user = SimpleNamespace(id=999, username="the_bot")
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock(spec=FSMContext)
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    settings_service = MagicMock()
    settings_service.get = AsyncMock(
        return_value=SimpleNamespace(
            timezone="Europe/Moscow",
            booking_horizon_days=31,
            allow_saturday=True,
            allow_sunday=True,
            availability_date_picker_days=31,
        )
    )

    await begin_window_creation_from_callback(callback, state, settings_service)

    actor = settings_service.get.await_args.args[0]
    assert actor.telegram_id == 101
    callback.message.answer.assert_awaited_once()
    callback.answer.assert_awaited_once()
