"""Service, date and open-window selection handlers."""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import BusinessType
from app.domain.errors import DomainError
from app.handlers.client.booking_common import available_dates
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.booking import (
    BookingCallback,
    booking_navigation_keyboard,
    dates_keyboard,
    masters_keyboard,
    services_keyboard,
    windows_keyboard,
)
from app.keyboards.client.main import CLIENT_BOOK_TEXT
from app.schemas.booking import BookingMasterOptions, ClientActor
from app.services.booking_service import BookingService
from app.services.presentation_service import PresentationService
from app.states.booking import BookingFlow

router = Router(name="client.booking_browse")


def should_show_master_selection(
    business_type: BusinessType,
    options: BookingMasterOptions,
) -> bool:
    """Return whether this booking needs an explicit master-selection step."""

    return (
        business_type is BusinessType.SALON
        and options.selection_enabled
        and len(options.masters) > 1
    )


async def start_booking(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
    *,
    actor: ClientActor | None = None,
) -> None:
    if actor is None:
        if message.from_user is None:
            return
        actor = actor_from_telegram(message.from_user)
    try:
        services = await booking_service.list_active_services(actor)
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
    presentation_service: PresentationService,
) -> None:
    try:
        options = await booking_service.list_bookable_masters(
            actor_from_telegram(callback.from_user),
            callback_data.object_id,
        )
        business = await presentation_service.get_business()
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not options.masters:
        await callback.answer("Для этой услуги пока нет доступных мастеров.", show_alert=True)
        return
    show_selection = should_show_master_selection(business.business_type, options)
    await state.update_data(
        service_id=callback_data.object_id,
        master_selection_shown=show_selection,
    )
    if show_selection:
        await state.set_state(BookingFlow.master)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Выберите мастера или доверьте выбор нам:",
                reply_markup=masters_keyboard(options.masters),
            )
        await callback.answer()
        return
    selected_staff_id = options.masters[0].id if len(options.masters) == 1 else None
    await state.update_data(staff_member_id=selected_staff_id)
    should_answer = await _show_dates(
        callback,
        state,
        booking_service,
        callback_data.object_id,
        selected_staff_id,
    )
    if should_answer:
        await callback.answer()


@router.callback_query(
    BookingFlow.master,
    BookingCallback.filter(F.action == "master"),
)
async def select_master(
    callback: CallbackQuery,
    callback_data: BookingCallback,
    state: FSMContext,
    booking_service: BookingService,
    presentation_service: PresentationService,
) -> None:
    data = await state.get_data()
    try:
        service_id = int(str(data["service_id"]))
        options = await booking_service.list_bookable_masters(
            actor_from_telegram(callback.from_user),
            service_id,
        )
        business = await presentation_service.get_business()
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    selection_available = should_show_master_selection(business.business_type, options)
    valid_ids = {master.id for master in options.masters}
    if not selection_available or (
        callback_data.object_id != 0 and callback_data.object_id not in valid_ids
    ):
        await callback.answer("Выбор мастера больше недоступен.", show_alert=True)
        return
    selected_staff_id = callback_data.object_id or None
    await state.update_data(staff_member_id=selected_staff_id)
    should_answer = await _show_dates(
        callback,
        state,
        booking_service,
        service_id,
        selected_staff_id,
    )
    if should_answer:
        await callback.answer()


async def _show_dates(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
    service_id: int,
    staff_member_id: int | None,
) -> bool:
    try:
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
            staff_member_id=staff_member_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return False
    dates = available_dates(availability.windows)
    if not dates:
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Сейчас подходящих свободных окон нет. "
                "Проверьте другую услугу или попробуйте позже."
            )
        return True
    data = await state.get_data()
    back_action = "back_masters" if data.get("master_selection_shown") else "back_services"
    await state.set_state(BookingFlow.date)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите дату:",
            reply_markup=dates_keyboard(dates, back_action=back_action),
        )
    return True


@router.callback_query(BookingCallback.filter(F.action == "back_masters"))
async def return_to_masters(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
    presentation_service: PresentationService,
) -> None:
    data = await state.get_data()
    try:
        service_id = int(str(data["service_id"]))
        options = await booking_service.list_bookable_masters(
            actor_from_telegram(callback.from_user),
            service_id,
        )
        business = await presentation_service.get_business()
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not should_show_master_selection(business.business_type, options):
        await callback.answer("Выбор мастера больше недоступен.", show_alert=True)
        return
    await state.set_state(BookingFlow.master)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите мастера или доверьте выбор нам:",
            reply_markup=masters_keyboard(options.masters),
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
        service_id = int(str(data["service_id"]))
        staff_member_id = (
            int(str(data["staff_member_id"])) if data.get("staff_member_id") is not None else None
        )
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
            staff_member_id=staff_member_id,
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
        service_id = int(str(data["service_id"]))
        staff_member_id = (
            int(str(data["staff_member_id"])) if data.get("staff_member_id") is not None else None
        )
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
            staff_member_id=staff_member_id,
        )
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.set_state(BookingFlow.date)
    if isinstance(callback.message, Message):
        back_action = "back_masters" if data.get("master_selection_shown") else "back_services"
        await callback.message.edit_text(
            "Выберите дату:",
            reply_markup=dates_keyboard(
                available_dates(availability.windows),
                back_action=back_action,
            ),
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
        service_id = int(str(data["service_id"]))
        local_date = date.fromisoformat(str(data["local_date"]))
        staff_member_id = (
            int(str(data["staff_member_id"])) if data.get("staff_member_id") is not None else None
        )
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
            staff_member_id=staff_member_id,
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
