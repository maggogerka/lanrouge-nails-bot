"""Client catalog cards, direct booking, and stale-menu recovery."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from app.handlers.client.menu import book_with_master, refresh_stale_optional_menu
from app.keyboards.client.main import CLIENT_REVIEWS_TEXT
from app.keyboards.client.masters import PublicMasterCallback, public_master_keyboard
from app.schemas.menu import MenuCapabilities
from app.schemas.service import ServiceView


def service_view() -> ServiceView:
    return ServiceView(
        id=4,
        name="Маникюр",
        description=None,
        price=Decimal("2500.00"),
        duration_min_minutes=60,
        duration_max_minutes=90,
        prepayment_amount=Decimal("0.00"),
        is_active=True,
    )


def test_master_card_has_direct_booking_action() -> None:
    keyboard = public_master_keyboard(9)
    button = keyboard.inline_keyboard[0][0]

    assert "Записаться" in button.text
    assert "9" in str(button.callback_data)


@pytest.mark.asyncio
async def test_direct_master_booking_preserves_preferred_master() -> None:
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=12, is_bot=False, first_name="Тест")
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock(spec=FSMContext)
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    booking = SimpleNamespace(
        list_active_services_for_master=AsyncMock(return_value=[service_view()])
    )

    await book_with_master(
        callback,
        PublicMasterCallback(action="book", staff_member_id=9),
        state,
        booking,
    )

    state.update_data.assert_awaited_with(preferred_staff_member_id=9)
    callback.message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_stale_disabled_feature_refreshes_reply_keyboard() -> None:
    message = MagicMock(spec=Message)
    message.text = CLIENT_REVIEWS_TEXT
    message.answer = AsyncMock()
    menu_service = SimpleNamespace(
        get_capabilities=AsyncMock(return_value=MenuCapabilities(reviews_visible=False))
    )

    await refresh_stale_optional_menu(message, menu_service)

    markup = message.answer.await_args.kwargs["reply_markup"]
    labels = {button.text for row in markup.keyboard for button in row}
    assert CLIENT_REVIEWS_TEXT not in labels
