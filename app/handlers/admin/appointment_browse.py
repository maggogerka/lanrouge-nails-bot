"""Administrator schedule, details, cancellation and visit confirmation."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from app.domain.errors import DomainError
from app.handlers.admin.appointment_common import render_admin_appointment
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.appointments import (
    AdminAppointmentCallback,
    admin_appointment_details_keyboard,
    admin_appointment_list_keyboard,
    admin_cancel_keyboard,
    admin_reference_delete_keyboard,
)
from app.keyboards.admin.main import ADMIN_TODAY_TEXT, ADMIN_UPCOMING_TEXT
from app.schemas.appointment import AdminAppointmentView
from app.services.appointment_service import AppointmentService
from app.services.reference_cleanup_service import ReferenceCleanupService
from app.utils.pagination import paginate_sequence
from app.utils.telegram import edit_text_safely

router = Router(name="admin.appointment_browse")
_APPOINTMENTS_PAGE_SIZE = 8


async def _show_schedule(
    target: Message | CallbackQuery,
    appointment_service: AppointmentService,
    *,
    today: bool,
    page: int = 1,
) -> None:
    if target.from_user is None:
        return
    actor = actor_from_telegram(target.from_user)
    appointments = (
        await appointment_service.list_admin_today(actor)
        if today
        else await appointment_service.list_admin_upcoming(actor)
    )
    paged = paginate_sequence(appointments, page=page, page_size=_APPOINTMENTS_PAGE_SIZE)
    label = "Записей на сегодня нет." if today else "Ближайших записей нет."
    if appointments:
        label = (
            "📅 Записи на сегодня по времени:"
            if today
            else "🗓 Ближайшие записи сгруппированы по дням:"
        )
        label += f"\nСтраница {paged.page} из {paged.pages} · всего {paged.total}."
    keyboard = admin_appointment_list_keyboard(
        list(paged.items),
        list_action="today" if today else "upcoming",
        page=paged.page,
        pages=paged.pages,
    )
    if isinstance(target, CallbackQuery):
        changed = True
        if isinstance(target.message, Message):
            changed = await edit_text_safely(target.message, label, reply_markup=keyboard)
        await target.answer(None if changed else "Расписание уже актуально.")
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
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
) -> None:
    await _show_schedule(callback, appointment_service, today=True, page=callback_data.page)


@router.callback_query(AdminAppointmentCallback.filter(F.action == "upcoming"))
async def show_upcoming_callback(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
) -> None:
    await _show_schedule(callback, appointment_service, today=False, page=callback_data.page)


@router.callback_query(AdminAppointmentCallback.filter(F.action == "day_label"))
async def explain_schedule_day(callback: CallbackQuery) -> None:
    await callback.answer("Выберите запись под этой датой.")


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
        await edit_text_safely(
            callback.message,
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


@router.callback_query(AdminAppointmentCallback.filter(F.action == "references"))
async def show_appointment_reference_media(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
) -> None:
    try:
        media = await appointment_service.list_admin_reference_media(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not media:
        await callback.answer("К этой записи референсы не прикреплены.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        if len(media) == 1:
            await callback.message.answer_photo(media[0].telegram_file_id)
        else:
            await callback.message.answer_media_group(
                [InputMediaPhoto(media=item.telegram_file_id) for item in media]
            )
    await callback.answer()


@router.callback_query(AdminAppointmentCallback.filter(F.action == "references_delete_prompt"))
async def prompt_reference_deletion(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Удалить все фотографии-референсы этой записи? "
            "Бот необратимо удалит Telegram file ID из своей базы и потеряет доступ к файлам. "
            "Это действие не удаляет копии с серверов Telegram.",
            reply_markup=admin_reference_delete_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.callback_query(AdminAppointmentCallback.filter(F.action == "references_delete_confirm"))
async def confirm_reference_deletion(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
    reference_cleanup_service: ReferenceCleanupService,
    correlation_id: str,
) -> None:
    try:
        requested = await appointment_service.request_admin_reference_cleanup(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            correlation_id=correlation_id,
        )
        result = await reference_cleanup_service.cleanup_appointment_now(
            callback_data.appointment_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Доступ приложения к референсам удалён. "
            f"Обработано: {result.deleted} из {requested}. "
            "Это не означает удаление файла с серверов Telegram."
        )
    await callback.answer("Референсы очищены.")


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


@router.callback_query(AdminAppointmentCallback.filter(F.action == "no_show"))
async def mark_client_no_show(
    callback: CallbackQuery,
    callback_data: AdminAppointmentCallback,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    try:
        appointment = await appointment_service.mark_no_show(
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
    await callback.answer("Неявка отмечена.")


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
