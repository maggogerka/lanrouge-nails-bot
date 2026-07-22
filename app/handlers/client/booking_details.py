"""Client details, confirmation and transactional booking handlers."""

from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from pydantic import ValidationError

from app.config import Settings
from app.domain.booking import normalize_phone
from app.domain.errors import BookingConflictError, DomainError
from app.handlers.client.booking_common import (
    available_dates,
    render_admin_new_booking,
    render_booking_confirmation,
    render_booking_receipt,
)
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.booking import (
    BOOKING_BACK_TEXT,
    BOOKING_CANCEL_TEXT,
    BookingCallback,
    appointment_links_keyboard,
    booking_navigation_keyboard,
    confirmation_keyboard,
    dates_keyboard,
    services_keyboard,
    windows_keyboard,
)
from app.keyboards.client.main import client_main_keyboard
from app.logging import log_event
from app.schemas.booking import BookingRequest
from app.services.booking_service import BookingService
from app.states.booking import BookingFlow

router = Router(name="client.booking_details")
logger = logging.getLogger(__name__)


@router.callback_query(BookingCallback.filter(F.action == "cancel"))
async def cancel_booking_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Оформление записи отменено.")
        await callback.message.answer("Главное меню:", reply_markup=client_main_keyboard())
    await callback.answer()


@router.message(
    StateFilter(*BookingFlow.__all_states__),
    F.text == BOOKING_CANCEL_TEXT,
)
async def cancel_booking_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Оформление записи отменено.", reply_markup=client_main_keyboard())


@router.message(
    StateFilter(BookingFlow.name, BookingFlow.phone, BookingFlow.comment),
    F.text == BOOKING_BACK_TEXT,
)
async def booking_back_message(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    if message.from_user is None:
        return
    current_state = await state.get_state()
    data = await state.get_data()
    if current_state == BookingFlow.name.state:
        try:
            service_id = int(data["service_id"])
            local_date = date.fromisoformat(str(data["local_date"]))
            availability = await booking_service.list_availability(
                actor_from_telegram(message.from_user),
                service_id,
                local_date=local_date,
            )
        except (DomainError, KeyError, ValueError) as exc:
            await message.answer(str(exc))
            return
        await state.set_state(BookingFlow.window)
        await message.answer(
            f"Выберите время на {local_date:%d.%m.%Y}:",
            reply_markup=windows_keyboard(availability.windows, local_date),
        )
    elif current_state == BookingFlow.phone.state:
        await state.set_state(BookingFlow.name)
        await message.answer("Как вас зовут?", reply_markup=booking_navigation_keyboard())
    else:
        await state.set_state(BookingFlow.phone)
        await message.answer(
            "Отправьте номер кнопкой ниже или введите вручную:",
            reply_markup=booking_navigation_keyboard(request_contact=True),
        )


@router.message(BookingFlow.name)
async def capture_client_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 255:
        await message.answer("Введите имя длиной от 1 до 255 символов.")
        return
    await state.update_data(client_name=name)
    await state.set_state(BookingFlow.phone)
    await message.answer(
        "Отправьте номер кнопкой ниже или введите вручную:",
        reply_markup=booking_navigation_keyboard(request_contact=True),
    )


@router.message(BookingFlow.phone)
async def capture_client_phone(message: Message, state: FSMContext) -> None:
    if message.contact is not None:
        if (
            message.contact.user_id is not None
            and message.from_user is not None
            and message.contact.user_id != message.from_user.id
        ):
            await message.answer("Пожалуйста, отправьте именно свой контакт или введите номер.")
            return
        raw_phone = message.contact.phone_number
    else:
        raw_phone = message.text or ""
    try:
        phone = normalize_phone(raw_phone)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(phone=phone)
    await state.set_state(BookingFlow.comment)
    await message.answer(
        "Добавьте комментарий к записи или отправьте «-», если комментария нет:",
        reply_markup=booking_navigation_keyboard(),
    )


@router.message(BookingFlow.comment)
async def capture_client_comment(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    if message.from_user is None:
        return
    raw_comment = (message.text or "").strip()
    comment = None if raw_comment == "-" else raw_comment
    if comment is not None and len(comment) > 2000:
        await message.answer("Комментарий не должен превышать 2000 символов.")
        return
    await state.update_data(client_comment=comment)
    data = await state.get_data()
    try:
        service_id = int(str(data["service_id"]))
        window_id = int(data["window_id"])
        local_date = date.fromisoformat(str(data["local_date"]))
        availability = await booking_service.list_availability(
            actor_from_telegram(message.from_user),
            service_id,
            local_date=local_date,
        )
        window = next(item for item in availability.windows if item.id == window_id)
        info = await booking_service.get_business_info(actor_from_telegram(message.from_user))
        client_name = str(data["client_name"])
    except (DomainError, KeyError, StopIteration, ValueError) as exc:
        await state.clear()
        await message.answer(str(exc) or "Выбранное время уже недоступно.")
        await message.answer("Главное меню:", reply_markup=client_main_keyboard())
        return
    await state.set_state(BookingFlow.confirm)
    await message.answer("Проверьте данные:", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        render_booking_confirmation(
            availability.service,
            window,
            info,
            client_name=client_name,
        ),
        reply_markup=confirmation_keyboard(),
    )


@router.callback_query(
    BookingFlow.confirm,
    BookingCallback.filter(F.action == "change"),
)
async def change_booking(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    services = await booking_service.list_active_services(actor_from_telegram(callback.from_user))
    await state.clear()
    await state.set_state(BookingFlow.service)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите услугу заново:",
            reply_markup=services_keyboard(services),
        )
    await callback.answer()


@router.callback_query(
    BookingFlow.confirm,
    BookingCallback.filter(F.action == "confirm"),
)
async def confirm_booking(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
    settings: Settings,
    bot: Bot,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    try:
        request = BookingRequest(
            service_id=data["service_id"],
            window_id=data["window_id"],
            client_name=data["client_name"],
            phone=data["phone"],
            client_comment=data.get("client_comment"),
        )
        receipt = await booking_service.book(
            actor_from_telegram(callback.from_user),
            request,
            correlation_id=correlation_id,
        )
    except BookingConflictError as exc:
        await _show_dates_after_conflict(callback, state, booking_service, data, str(exc))
        return
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_booking_receipt(receipt),
            reply_markup=appointment_links_keyboard(
                receipt.map_url,
                receipt.master_telegram_url,
            ),
        )
        await callback.message.answer("Главное меню:", reply_markup=client_main_keyboard())
    await callback.answer("Запись создана.")

    admin_text = render_admin_new_booking(receipt)
    for admin_telegram_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(admin_telegram_id, admin_text)
        except TelegramAPIError:
            log_event(
                logger,
                logging.WARNING,
                "booking.admin_notification_failed",
                appointment_id=receipt.appointment_id,
            )


async def _show_dates_after_conflict(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
    data: dict[str, object],
    message: str,
) -> None:
    try:
        service_id = int(str(data["service_id"]))
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
        )
    except (DomainError, KeyError, ValueError):
        await state.clear()
        await callback.answer(message, show_alert=True)
        return
    await state.set_state(BookingFlow.date)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            message + "\n\nВыберите другую дату:",
            reply_markup=dates_keyboard(available_dates(availability.windows)),
        )
    await callback.answer(message, show_alert=True)
