"""Client-owned appointment viewing, cancellation and rescheduling."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.domain.appointments import CLIENT_CHANGE_BLOCKED_MESSAGE
from app.domain.errors import CancellationDeadlineError, DomainError
from app.handlers.client.appointment_common import render_appointment
from app.handlers.client.booking_common import available_dates, render_booking_receipt
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.appointments import (
    AppointmentCallback,
    appointment_details_keyboard,
    appointment_list_keyboard,
    cancel_confirmation_keyboard,
    reschedule_confirmation_keyboard,
    reschedule_dates_keyboard,
    reschedule_windows_keyboard,
)
from app.keyboards.client.booking import appointment_links_keyboard
from app.keyboards.client.main import CLIENT_APPOINTMENTS_TEXT
from app.services.appointment_service import AppointmentService
from app.services.reschedule_service import RescheduleService

router = Router(name="client.appointments")


async def show_my_list(
    target: Message | CallbackQuery,
    appointment_service: AppointmentService,
) -> None:
    if target.from_user is None:
        return
    actor = actor_from_telegram(target.from_user)
    appointments = await appointment_service.list_my(actor)
    text = "У вас пока нет будущих записей." if not appointments else "Ваши будущие записи:"
    keyboard = appointment_list_keyboard(appointments)
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            await target.message.edit_text(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(F.text == CLIENT_APPOINTMENTS_TEXT)
async def list_my_appointments(
    message: Message,
    appointment_service: AppointmentService,
) -> None:
    if message.from_user is None:
        return
    await show_my_list(message, appointment_service)


@router.callback_query(AppointmentCallback.filter(F.action == "list"))
async def list_my_appointments_callback(
    callback: CallbackQuery,
    appointment_service: AppointmentService,
) -> None:
    await show_my_list(callback, appointment_service)


@router.callback_query(AppointmentCallback.filter(F.action == "view"))
async def show_my_appointment(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    appointment_service: AppointmentService,
) -> None:
    try:
        appointment = await appointment_service.get_my(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_appointment(appointment),
            reply_markup=appointment_details_keyboard(appointment),
        )
    await callback.answer()


@router.callback_query(AppointmentCallback.filter(F.action == "cancel_prompt"))
async def prompt_my_cancellation(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Отменить запись? Это действие сохранится в истории.",
            reply_markup=cancel_confirmation_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.callback_query(AppointmentCallback.filter(F.action == "cancel_confirm"))
async def cancel_my_appointment(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    try:
        await appointment_service.cancel_my(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            correlation_id=correlation_id,
        )
    except CancellationDeadlineError:
        await _show_deadline_message(callback)
        return
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await show_my_list(callback, appointment_service)


@router.callback_query(AppointmentCallback.filter(F.action == "reschedule"))
async def begin_my_reschedule(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    reschedule_service: RescheduleService,
) -> None:
    try:
        options = await reschedule_service.list_my_options(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
    except CancellationDeadlineError:
        await _show_deadline_message(callback)
        return
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    dates = available_dates(options.windows)
    if not dates:
        await callback.answer("Подходящих свободных окон пока нет.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите новую дату:",
            reply_markup=reschedule_dates_keyboard(options.appointment.id, dates),
        )
    await callback.answer()


@router.callback_query(AppointmentCallback.filter(F.action == "rdate"))
async def select_my_reschedule_date(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    reschedule_service: RescheduleService,
) -> None:
    try:
        local_date = date.fromordinal(callback_data.object_id)
        options = await reschedule_service.list_my_options(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
    except (CancellationDeadlineError, DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    windows = [
        window
        for window in options.windows
        if window.start_at.astimezone(ZoneInfo(window.timezone)).date() == local_date
    ]
    if not windows:
        await callback.answer("На этой дате больше нет свободного времени.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"Выберите новое время на {local_date:%d.%m.%Y}:",
            reply_markup=reschedule_windows_keyboard(callback_data.appointment_id, windows),
        )
    await callback.answer()


@router.callback_query(AppointmentCallback.filter(F.action == "rwindow"))
async def prompt_my_reschedule(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    reschedule_service: RescheduleService,
) -> None:
    try:
        options = await reschedule_service.list_my_options(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
        window = next(item for item in options.windows if item.id == callback_data.object_id)
    except (DomainError, StopIteration) as exc:
        await callback.answer(str(exc) or "Окно уже недоступно.", show_alert=True)
        return
    local = window.start_at.astimezone(ZoneInfo(window.timezone))
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"Перенести запись на {local:%d.%m.%Y в %H:%M}?",
            reply_markup=reschedule_confirmation_keyboard(
                callback_data.appointment_id,
                window.id,
            ),
        )
    await callback.answer()


@router.callback_query(AppointmentCallback.filter(F.action == "rconfirm"))
async def confirm_my_reschedule(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    reschedule_service: RescheduleService,
    correlation_id: str,
) -> None:
    try:
        receipt = await reschedule_service.reschedule_my(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            callback_data.object_id,
            correlation_id=correlation_id,
        )
    except CancellationDeadlineError:
        await _show_deadline_message(callback)
        return
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Запись перенесена.\n\n" + render_booking_receipt(receipt),
            reply_markup=appointment_links_keyboard(
                receipt.map_url,
                receipt.master_telegram_url,
            ),
        )
    await callback.answer("Запись перенесена.")


async def _show_deadline_message(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            CLIENT_CHANGE_BLOCKED_MESSAGE,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Написать мастеру",
                            url="https://t.me/lanrouge",
                        )
                    ]
                ]
            ),
        )
    await callback.answer("Самостоятельное изменение уже недоступно.", show_alert=True)
