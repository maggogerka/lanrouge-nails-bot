"""Administrator schedule, details, cancellation and visit confirmation."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError
from app.handlers.admin.appointment_common import render_admin_appointment
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.appointments import (
    AdminAppointmentCallback,
    admin_appointment_details_keyboard,
    admin_appointment_list_keyboard,
    admin_cancel_keyboard,
)
from app.keyboards.admin.main import ADMIN_TODAY_TEXT, ADMIN_UPCOMING_TEXT
from app.schemas.appointment import AdminAppointmentView
from app.services.appointment_service import AppointmentService

router = Router(name="admin.appointment_browse")


async def _show_schedule(
    target: Message | CallbackQuery,
    appointment_service: AppointmentService,
    *,
    today: bool,
) -> None:
    if target.from_user is None:
        return
    actor = actor_from_telegram(target.from_user)
    appointments = (
        await appointment_service.list_admin_today(actor)
        if today
        else await appointment_service.list_admin_upcoming(actor)
    )
    label = "Записей на сегодня нет." if today else "Ближайших записей нет."
    if appointments:
        label = "Записи на сегодня:" if today else "Ближайшие записи:"
    keyboard = admin_appointment_list_keyboard(
        appointments,
        list_action="today" if today else "upcoming",
    )
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            await target.message.edit_text(label, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(label, reply_markup=keyboard)


@router.message(F.text == ADMIN_TODAY_TEXT)
async def show_today(message: Message, appointment_service: AppointmentService) -> None:
    if message.from_user is None:
        return
    await _show_schedule(message, appointment_service, today=True)


@router.message(F.text == ADMIN_UPCOMING_TEXT)
async def show_upcoming(message: Message, appointment_service: AppointmentService) -> None:
    if message.from_user is None:
        return
    await _show_schedule(message, appointment_service, today=False)


@router.callback_query(AdminAppointmentCallback.filter(F.action == "today"))
async def show_today_callback(
    callback: CallbackQuery,
    appointment_service: AppointmentService,
) -> None:
    await _show_schedule(callback, appointment_service, today=True)


@router.callback_query(AdminAppointmentCallback.filter(F.action == "upcoming"))
async def show_upcoming_callback(
    callback: CallbackQuery,
    appointment_service: AppointmentService,
) -> None:
    await _show_schedule(callback, appointment_service, today=False)


async def _show_details(
    callback: CallbackQuery,
    appointment_service: AppointmentService,
    appointment_id: int,
) -> AdminAppointmentView | None:
    try:
        appointment = await appointment_service.get_admin(
            actor_from_telegram(callback.from_user),
            appointment_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return None
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_admin_appointment(appointment),
            reply_markup=admin_appointment_details_keyboard(appointment),
        )
    await callback.answer()
    return appointment


@router.callback_query(AdminAppointmentCallback.filter(F.action == "view"))
async def show_appointment_details(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
) -> None:
    await _show_details(callback, appointment_service, callback_data.appointment_id)


@router.callback_query(AdminAppointmentCallback.filter(F.action == "confirm"))
async def confirm_client_visit(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    try:
        appointment = await appointment_service.confirm_visit(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_admin_appointment(appointment),
            reply_markup=admin_appointment_details_keyboard(appointment),
        )
    await callback.answer("Визит подтверждён.")


@router.callback_query(AdminAppointmentCallback.filter(F.action == "complete"))
async def complete_client_visit(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    try:
        appointment = await appointment_service.complete_visit(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_admin_appointment(appointment),
            reply_markup=admin_appointment_details_keyboard(appointment),
        )
    await callback.answer("Визит завершён. Запрос отзыва поставлен в очередь.")


@router.callback_query(AdminAppointmentCallback.filter(F.action == "cancel_prompt"))
async def prompt_admin_cancellation(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "После отмены открыть это окно для новой записи или оставить закрытым?",
            reply_markup=admin_cancel_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.callback_query(
    AdminAppointmentCallback.filter(F.action.in_({"cancel_open", "cancel_close"}))
)
async def cancel_appointment_as_admin(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    try:
        appointment = await appointment_service.cancel_admin(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            reopen_window=callback_data.action == "cancel_open",
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(render_admin_appointment(appointment))
    await callback.answer("Запись отменена.")
