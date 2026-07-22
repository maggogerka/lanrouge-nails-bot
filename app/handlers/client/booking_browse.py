"""Service, date and open-window selection handlers."""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError
from app.handlers.client.booking_common import available_dates
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.booking import (
    BookingCallback,
    booking_navigation_keyboard,
    dates_keyboard,
    services_keyboard,
    windows_keyboard,
)
from app.keyboards.client.main import CLIENT_BOOK_TEXT
from app.services.booking_service import BookingService
from app.states.booking import BookingFlow

router = Router(name="client.booking_browse")


async def start_booking(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    if message.from_user is None:
        return
    try:
        services = await booking_service.list_active_services(
            actor_from_telegram(message.from_user)
        )
    except DomainError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    if not services:
        await message.answer("Сейчас нет активных услуг для записи.")
        return
    await state.set_state(BookingFlow.service)
    await message.answer("Выберите услугу:", reply_markup=services_keyboard(services))


@router.message(F.text == CLIENT_BOOK_TEXT)
async def begin_booking(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    await start_booking(message, state, booking_service)


@router.callback_query(BookingCallback.filter(F.action == "back_services"))
async def return_to_services(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    services = await booking_service.list_active_services(actor_from_telegram(callback.from_user))
    await state.clear()
    await state.set_state(BookingFlow.service)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите услугу:",
            reply_markup=services_keyboard(services),
        )
    await callback.answer()


@router.callback_query(
    BookingFlow.service,
    BookingCallback.filter(F.action == "service"),
)
async def select_service(
    callback: CallbackQuery,
    callback_data: BookingCallback,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    try:
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            callback_data.object_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    dates = available_dates(availability.windows)
    if not dates:
        await callback.answer("Для этой услуги пока нет свободных дат.", show_alert=True)
        return
    await state.update_data(service_id=callback_data.object_id)
    await state.set_state(BookingFlow.date)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите дату:",
            reply_markup=dates_keyboard(dates),
        )
    await callback.answer()


@router.callback_query(
    BookingFlow.date,
    BookingCallback.filter(F.action == "date"),
)
async def select_date(
    callback: CallbackQuery,
    callback_data: BookingCallback,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    data = await state.get_data()
    try:
        local_date = date.fromordinal(callback_data.object_id)
        service_id = int(data["service_id"])
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
            local_date=local_date,
        )
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not availability.windows:
        await callback.answer("На этой дате больше нет свободного времени.", show_alert=True)
        return
    await state.update_data(local_date=local_date.isoformat())
    await state.set_state(BookingFlow.window)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"Выберите время на {local_date:%d.%m.%Y}:",
            reply_markup=windows_keyboard(availability.windows, local_date),
        )
    await callback.answer()


@router.callback_query(BookingCallback.filter(F.action == "back_dates"))
async def return_to_dates(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    data = await state.get_data()
    try:
        service_id = int(data["service_id"])
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
        )
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.set_state(BookingFlow.date)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите дату:",
            reply_markup=dates_keyboard(available_dates(availability.windows)),
        )
    await callback.answer()


@router.callback_query(
    BookingFlow.window,
    BookingCallback.filter(F.action == "window"),
)
async def select_window(
    callback: CallbackQuery,
    callback_data: BookingCallback,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    data = await state.get_data()
    try:
        service_id = int(data["service_id"])
        local_date = date.fromisoformat(str(data["local_date"]))
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
            local_date=local_date,
        )
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if callback_data.object_id not in {window.id for window in availability.windows}:
        await callback.answer("Это время уже недоступно.", show_alert=True)
        return
    await state.update_data(window_id=callback_data.object_id)
    await state.set_state(BookingFlow.name)
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Время выбрано.")
        await callback.message.answer(
            "Как вас зовут?",
            reply_markup=booking_navigation_keyboard(),
        )
    await callback.answer()
