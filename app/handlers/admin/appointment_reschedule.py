"""Administrator reschedule callbacks without the client deadline."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError
from app.handlers.admin.appointment_common import render_admin_appointment
from app.handlers.admin.service_common import actor_from_telegram
from app.handlers.client.booking_common import available_dates
from app.keyboards.admin.appointments import (
    AdminAppointmentCallback,
    admin_appointment_details_keyboard,
    admin_reschedule_confirmation_keyboard,
    admin_reschedule_dates_keyboard,
    admin_reschedule_windows_keyboard,
)
from app.services.appointment_service import AppointmentService
from app.services.reschedule_service import RescheduleService

router = Router(name="admin.appointment_reschedule")


@router.callback_query(AdminAppointmentCallback.filter(F.action == "reschedule"))
async def begin_admin_reschedule(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    reschedule_service: RescheduleService,
) -> None:
    try:
        options = await reschedule_service.list_admin_options(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
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
            reply_markup=admin_reschedule_dates_keyboard(options.appointment.id, dates),
        )
    await callback.answer()


@router.callback_query(AdminAppointmentCallback.filter(F.action == "rdate"))
async def select_admin_reschedule_date(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    reschedule_service: RescheduleService,
) -> None:
    try:
        local_date = date.fromordinal(callback_data.object_id)
        options = await reschedule_service.list_admin_options(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
    except (DomainError, ValueError) as exc:
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
            f"Выберите время на {local_date:%d.%m.%Y}:",
            reply_markup=admin_reschedule_windows_keyboard(
                callback_data.appointment_id,
                windows,
            ),
        )
    await callback.answer()


@router.callback_query(AdminAppointmentCallback.filter(F.action == "rwindow"))
async def prompt_admin_reschedule(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    reschedule_service: RescheduleService,
) -> None:
    try:
        options = await reschedule_service.list_admin_options(
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
            reply_markup=admin_reschedule_confirmation_keyboard(
                callback_data.appointment_id,
                window.id,
            ),
        )
    await callback.answer()


@router.callback_query(AdminAppointmentCallback.filter(F.action == "rconfirm"))
async def confirm_admin_reschedule(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    reschedule_service: RescheduleService,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    try:
        receipt = await reschedule_service.reschedule_admin(
            actor,
            callback_data.appointment_id,
            callback_data.object_id,
            correlation_id=correlation_id,
        )
        appointment = await appointment_service.get_admin(actor, receipt.appointment_id)
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Запись перенесена.\n\n" + render_admin_appointment(appointment),
            reply_markup=admin_appointment_details_keyboard(appointment),
        )
    await callback.answer("Запись перенесена.")
