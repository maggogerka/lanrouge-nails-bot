"""Actions originating from persistent reminder messages."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError
from app.handlers.client.appointment_common import render_appointment
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.appointments import appointment_details_keyboard
from app.keyboards.client.reminders import ReminderCallback
from app.services.appointment_service import AppointmentService

router = Router(name="client.reminders")


@router.callback_query(ReminderCallback.filter(F.action == "confirm"))
async def confirm_visit_from_reminder(
    callback: CallbackQuery,
    callback_data: ReminderCallback,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    try:
        appointment = await appointment_service.confirm_my_visit(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Спасибо, визит подтверждён!\n\n" + render_appointment(appointment),
            reply_markup=appointment_details_keyboard(appointment),
        )
    await callback.answer("Визит подтверждён.")
