"""Client-owned appointment viewing, cancellation and rescheduling."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InputMediaPhoto,
    Message,
)

from app.domain.errors import CancellationDeadlineError, DomainError
from app.handlers.client.appointment_common import render_appointment
from app.handlers.client.booking_common import available_dates, render_booking_receipt
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.appointments import (
    AppointmentCallback,
    appointment_details_keyboard,
    appointment_list_keyboard,
    cancel_confirmation_keyboard,
    clear_references_confirmation_keyboard,
    reference_edit_keyboard,
    reschedule_confirmation_keyboard,
    reschedule_dates_keyboard,
    reschedule_windows_keyboard,
)
from app.keyboards.client.booking import appointment_links_keyboard
from app.keyboards.client.main import CLIENT_APPOINTMENTS_TEXT
from app.keyboards.client.presentation import business_links_keyboard
from app.schemas.booking import ReferenceMediaDraft
from app.services.appointment_service import AppointmentService
from app.services.presentation_service import PresentationService
from app.services.reschedule_service import RescheduleService
from app.states.booking import BookingReferenceEdit
from app.utils.pagination import paginate_sequence
from app.utils.telegram import edit_text_safely

router = Router(name="client.appointments")
_APPOINTMENTS_PAGE_SIZE = 8


async def show_my_list(
    target: Message | CallbackQuery,
    appointment_service: AppointmentService,
    *,
    page: int = 1,
) -> None:
    if target.from_user is None:
        return
    actor = actor_from_telegram(target.from_user)
    appointments = await appointment_service.list_my(actor)
    paged = paginate_sequence(appointments, page=page, page_size=_APPOINTMENTS_PAGE_SIZE)
    text = (
        "У вас пока нет будущих записей."
        if not appointments
        else (
            f"Ваши будущие записи · страница {paged.page} из {paged.pages} · всего {paged.total}:"
        )
    )
    keyboard = appointment_list_keyboard(list(paged.items), page=paged.page, pages=paged.pages)
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            await edit_text_safely(target.message, text, reply_markup=keyboard)
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
    callback_data: AppointmentCallback,
    appointment_service: AppointmentService,
) -> None:
    await show_my_list(callback, appointment_service, page=callback_data.page)


@router.callback_query(AppointmentCallback.filter(F.action == "view"))
async def show_my_appointment(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    appointment_service: AppointmentService,
    state: FSMContext,
) -> None:
    await state.clear()
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


@router.callback_query(AppointmentCallback.filter(F.action == "references_add"))
async def begin_reference_edit(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    state: FSMContext,
    appointment_service: AppointmentService,
) -> None:
    try:
        await appointment_service.list_my_reference_media(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.set_state(BookingReferenceEdit.uploading)
    await state.set_data({"reference_appointment_id": callback_data.appointment_id})
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Отправьте одну или несколько фотографий. Альбом поддерживается.\n\n"
            "Когда закончите, нажмите «Завершить».",
            reply_markup=reference_edit_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.message(BookingReferenceEdit.uploading, F.photo)
async def add_reference_to_existing_appointment(
    message: Message,
    state: FSMContext,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    if message.from_user is None or not message.photo:
        return
    data = await state.get_data()
    try:
        appointment_id = int(data["reference_appointment_id"])
        await appointment_service.add_my_reference_media(
            actor_from_telegram(message.from_user),
            appointment_id,
            ReferenceMediaDraft(
                telegram_file_id=message.photo[-1].file_id,
                telegram_file_unique_id=message.photo[-1].file_unique_id,
            ),
            correlation_id=correlation_id,
        )
    except (DomainError, KeyError, ValueError) as exc:
        await message.answer(str(exc))
        return
    if message.media_group_id is None:
        await message.answer(
            "Фотография добавлена.",
            reply_markup=reference_edit_keyboard(appointment_id),
        )


@router.message(BookingReferenceEdit.uploading)
async def reject_non_photo_reference_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    appointment_id = int(data.get("reference_appointment_id", 0))
    await message.answer(
        "Отправьте фотографию или завершите редактирование.",
        reply_markup=reference_edit_keyboard(appointment_id),
    )


@router.callback_query(AppointmentCallback.filter(F.action == "references_clear_prompt"))
async def prompt_clear_references(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Удалить все референсы этой записи?",
            reply_markup=clear_references_confirmation_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.callback_query(AppointmentCallback.filter(F.action == "references_clear"))
async def clear_references(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    appointment_service: AppointmentService,
    correlation_id: str,
) -> None:
    try:
        removed = await appointment_service.clear_my_reference_media(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(
        "Референсы удалены." if removed else "У записи не было референсов.",
        show_alert=True,
    )
    if isinstance(callback.message, Message):
        appointment = await appointment_service.get_my(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
        await callback.message.edit_text(
            render_appointment(appointment),
            reply_markup=appointment_details_keyboard(appointment),
        )


@router.callback_query(AppointmentCallback.filter(F.action == "references"))
async def show_my_reference_media(
    callback: CallbackQuery,
    callback_data: AppointmentCallback,
    appointment_service: AppointmentService,
) -> None:
    try:
        media = await appointment_service.list_my_reference_media(
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
    presentation_service: PresentationService,
    correlation_id: str,
) -> None:
    try:
        await appointment_service.cancel_my(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            correlation_id=correlation_id,
        )
    except CancellationDeadlineError as exc:
        await _show_deadline_message(callback, presentation_service, str(exc))
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
    presentation_service: PresentationService,
) -> None:
    try:
        options = await reschedule_service.list_my_options(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
        )
    except CancellationDeadlineError as exc:
        await _show_deadline_message(callback, presentation_service, str(exc))
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
    presentation_service: PresentationService,
    correlation_id: str,
) -> None:
    try:
        receipt = await reschedule_service.reschedule_my(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            callback_data.object_id,
            correlation_id=correlation_id,
        )
    except CancellationDeadlineError as exc:
        await _show_deadline_message(callback, presentation_service, str(exc))
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


async def _show_deadline_message(
    callback: CallbackQuery,
    presentation_service: PresentationService,
    message: str,
) -> None:
    if isinstance(callback.message, Message):
        business = await presentation_service.get_business()
        await callback.message.edit_text(
            message,
            reply_markup=business_links_keyboard(business),
        )
    await callback.answer("Самостоятельное изменение уже недоступно.", show_alert=True)
