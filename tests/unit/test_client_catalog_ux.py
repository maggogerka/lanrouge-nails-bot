"""Client catalog cards, direct booking, and stale-menu recovery."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from app.handlers.client.booking_browse import (
    _render_service_card,
    browse_service_page,
    return_to_services,
    show_service_cards,
)
from app.handlers.client.booking_details import cancel_booking_callback
from app.handlers.client.menu import (
    _render_master_card,
    book_with_master,
    refresh_stale_optional_menu,
)
from app.keyboards.client.booking import BookingCallback
from app.keyboards.client.main import CLIENT_REVIEWS_TEXT
from app.keyboards.client.masters import PublicMasterCallback, public_master_keyboard
from app.schemas.menu import MenuCapabilities
from app.schemas.presentation import PublicMasterPresentation
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
async def test_master_photo_is_attached_to_paginated_card_without_photo_button() -> None:
    message = MagicMock(spec=Message)
    message.photo = None
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock(return_value=MagicMock(spec=Message))
    state = MagicMock(spec=FSMContext)
    state.update_data = AsyncMock()
    master = PublicMasterPresentation(
        staff_member_id=9,
        display_name="Руслана",
        telegram_photo_file_id="master-photo",
    )

    await _render_master_card(message, state, [master], page=1, edit=False)

    message.answer_photo.assert_awaited_once()
    assert message.answer_photo.await_args.args[0] == "master-photo"
    markup = message.answer_photo.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data or "" for row in markup.inline_keyboard for button in row]
    assert all("photo" not in value for value in callbacks)


@pytest.mark.asyncio
async def test_master_photo_pagination_edits_the_same_card() -> None:
    message = MagicMock(spec=Message)
    message.photo = [MagicMock()]
    message.edit_media = AsyncMock()
    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={"master_auxiliary_message_ids": []})
    state.update_data = AsyncMock()
    masters = [
        PublicMasterPresentation(
            staff_member_id=index,
            display_name=f"Мастер {index}",
            telegram_photo_file_id=f"photo-{index}",
        )
        for index in (1, 2)
    ]

    await _render_master_card(message, state, masters, page=2, edit=True)

    message.edit_media.assert_awaited_once()
    media = message.edit_media.await_args.args[0]
    assert media.media == "photo-2"


@pytest.mark.asyncio
async def test_cancel_booking_works_for_photo_card_and_returns_main_menu() -> None:
    callback = MagicMock(spec=CallbackQuery)
    callback.message = MagicMock(spec=Message)
    callback.message.photo = [MagicMock()]
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock(spec=FSMContext)
    state.clear = AsyncMock()
    menu_service = SimpleNamespace(get_capabilities=AsyncMock(return_value=MenuCapabilities()))

    await cancel_booking_callback(callback, state, menu_service)

    state.clear.assert_awaited_once_with()
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once()
    assert "отменено" in callback.message.answer.await_args.args[0].casefold()


@pytest.mark.asyncio
async def test_service_catalog_uses_one_paginated_message_instead_of_message_per_service() -> None:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.photo = None
    state = MagicMock(spec=FSMContext)
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    services = [service_view().model_copy(update={"id": index}) for index in range(1, 21)]

    await show_service_cards(message, state, services)

    message.answer.assert_awaited_once()
    markup = message.answer.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data or "" for row in markup.inline_keyboard for button in row]
    assert any("service_page" in value for value in callbacks)


@pytest.mark.asyncio
async def test_service_card_shows_photo_immediately_without_photo_button() -> None:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock()
    message.photo = None
    state = MagicMock(spec=FSMContext)
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    service = service_view().model_copy(update={"telegram_photo_file_id": "service-photo"})

    await show_service_cards(message, state, [service])

    message.answer_photo.assert_awaited_once()
    assert message.answer_photo.await_args.args[0] == "service-photo"
    markup = message.answer_photo.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data or "" for row in markup.inline_keyboard for button in row]
    assert all("service_photo" not in value for value in callbacks)
    assert any("service:" in value for value in callbacks)


@pytest.mark.asyncio
async def test_schema_maximum_service_card_is_split_without_losing_controls() -> None:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock(return_value=MagicMock(spec=Message))
    message.answer_photo = AsyncMock(return_value=MagicMock(spec=Message))
    message.photo = None
    state = MagicMock(spec=FSMContext)
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    service = service_view().model_copy(
        update={
            "name": "<&😀" + "Н" * 251,
            "description": "<&😀x" * 1000,
            "telegram_photo_file_id": "service-photo",
        }
    )

    await show_service_cards(message, state, [service])

    message.answer_photo.assert_awaited_once_with("service-photo")
    assert message.answer.await_count >= 2
    assert all(
        call.kwargs.get("reply_markup") is None for call in message.answer.await_args_list[:-1]
    )
    assert message.answer.await_args_list[-1].kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_current_service_page_indicator_is_a_noop() -> None:
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=12, is_bot=False, first_name="Тест")
    callback.message = MagicMock(spec=Message)
    callback.message.edit_media = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={"service_page": 1})
    state.update_data = AsyncMock()
    booking = SimpleNamespace(list_active_services=AsyncMock(return_value=[service_view()]))

    await browse_service_page(
        callback,
        BookingCallback(action="service_page", object_id=0, page=1),
        state,
        booking,
    )

    callback.message.edit_media.assert_not_awaited()
    callback.message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_return_to_services_handles_empty_catalog_without_index_error() -> None:
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=12, is_bot=False, first_name="Тест")
    callback.message = MagicMock(spec=Message)
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={"service_page": 1})
    state.clear = AsyncMock()
    booking = SimpleNamespace(list_active_services=AsyncMock(return_value=[]))

    await return_to_services(callback, state, booking)

    state.clear.assert_awaited_once_with()
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once_with("Сейчас нет активных услуг для записи.")
    callback.answer.assert_awaited_once_with()


def card_state() -> MagicMock:
    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={"service_auxiliary_message_ids": []})
    state.update_data = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_service_navigation_edits_photo_to_photo_in_place() -> None:
    message = MagicMock(spec=Message)
    message.photo = [MagicMock()]
    message.edit_media = AsyncMock()
    message.delete = AsyncMock()
    first = service_view().model_copy(update={"telegram_photo_file_id": "photo-1"})
    second = first.model_copy(update={"id": 5, "telegram_photo_file_id": "photo-2"})

    await _render_service_card(message, card_state(), [first, second], page=2, edit=True)

    message.edit_media.assert_awaited_once()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_navigation_replaces_photo_with_text_without_stale_keyboard() -> None:
    message = MagicMock(spec=Message)
    message.photo = [MagicMock()]
    message.delete = AsyncMock()
    message.answer = AsyncMock(return_value=MagicMock(spec=Message))

    await _render_service_card(message, card_state(), [service_view()], page=1, edit=True)

    message.delete.assert_awaited_once_with()
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_navigation_replaces_text_with_photo_without_stale_keyboard() -> None:
    message = MagicMock(spec=Message)
    message.photo = None
    message.delete = AsyncMock()
    message.answer_photo = AsyncMock(return_value=MagicMock(spec=Message))
    target = service_view().model_copy(update={"telegram_photo_file_id": "photo-1"})

    await _render_service_card(message, card_state(), [target], page=1, edit=True)

    message.delete.assert_awaited_once_with()
    message.answer_photo.assert_awaited_once()


@pytest.mark.asyncio
async def test_master_card_opens_generic_booking_flow() -> None:
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=12, is_bot=False, first_name="Тест")
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock(spec=FSMContext)
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    booking = SimpleNamespace(list_active_services=AsyncMock(return_value=[service_view()]))

    await book_with_master(
        callback,
        PublicMasterCallback(action="book", staff_member_id=9),
        state,
        booking,
    )

    state.update_data.assert_any_await(
        preferred_staff_member_id=None,
        service_page=1,
        service_auxiliary_message_ids=[],
    )
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
