"""Service, date and open-window selection handlers."""

from __future__ import annotations

from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import BusinessType
from app.domain.errors import DomainError
from app.handlers.client.booking_common import available_dates
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.booking import (
    BookingAddonCallback,
    BookingCallback,
    addons_keyboard,
    booking_navigation_keyboard,
    dates_keyboard,
    masters_keyboard,
    service_card_keyboard,
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
    await message.answer("Выберите услугу:")
    for service in services:
        description = escape(service.description or "Описание не добавлено.")
        duration = (
            f"{service.duration_min_minutes} мин."
            if service.duration_min_minutes == service.duration_max_minutes
            else f"{service.duration_min_minutes}–{service.duration_max_minutes} мин."
        )
        text = (
            f"<b>{escape(service.name)}</b>\n"
            f"{description}\n"
            f"Стоимость: {service.price:.2f} ₽\n"
            f"Длительность: {duration}"
        )
        keyboard = service_card_keyboard(service.id)
        if service.telegram_photo_file_id:
            await message.answer_photo(
                service.telegram_photo_file_id,
                caption=text[:1024],
                reply_markup=keyboard,
            )
        else:
            await message.answer(text[:4096], reply_markup=keyboard)


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
        addons = await booking_service.list_service_addons(
            actor_from_telegram(callback.from_user), callback_data.object_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.update_data(
        service_id=callback_data.object_id,
        addon_ids=[],
        addons_shown=bool(addons),
    )
    if addons:
        await state.set_state(BookingFlow.addons)
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Выберите дополнительные услуги или нажмите «Пропустить»:",
                reply_markup=addons_keyboard(addons, set()),
            )
        await callback.answer()
        return
    await _continue_after_addons(
        callback,
        state,
        booking_service,
        presentation_service,
        callback_data.object_id,
    )


@router.callback_query(BookingFlow.addons, BookingAddonCallback.filter())
async def select_addons(
    callback: CallbackQuery,
    callback_data: BookingAddonCallback,
    state: FSMContext,
    booking_service: BookingService,
    presentation_service: PresentationService,
) -> None:
    data = await state.get_data()
    try:
        service_id = int(str(data["service_id"]))
        addons = await booking_service.list_service_addons(
            actor_from_telegram(callback.from_user), service_id
        )
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    valid_ids = {addon.id for addon in addons}
    selected = {
        value
        for value in data.get("addon_ids", [])
        if isinstance(value, int) and value in valid_ids
    }
    if callback_data.action == "toggle":
        if callback_data.addon_id not in valid_ids:
            await callback.answer("Дополнительная услуга больше недоступна.", show_alert=True)
            return
        if callback_data.addon_id in selected:
            selected.remove(callback_data.addon_id)
        else:
            selected.add(callback_data.addon_id)
        await state.update_data(addon_ids=[addon.id for addon in addons if addon.id in selected])
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=addons_keyboard(addons, selected))
        await callback.answer("Выбор обновлён.")
        return
    if callback_data.action != "continue":
        await callback.answer("Эта кнопка устарела.", show_alert=True)
        return
    await state.update_data(addon_ids=[addon.id for addon in addons if addon.id in selected])
    await _continue_after_addons(callback, state, booking_service, presentation_service, service_id)


async def _continue_after_addons(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
    presentation_service: PresentationService,
    service_id: int,
) -> None:
    try:
        options = await booking_service.list_bookable_masters(
            actor_from_telegram(callback.from_user), service_id
        )
        business = await presentation_service.get_business()
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not options.masters:
        await callback.answer("Для этой услуги пока нет доступных мастеров.", show_alert=True)
        return
    show_selection = should_show_master_selection(business.business_type, options)
    await state.update_data(master_selection_shown=show_selection)
    if show_selection:
        await state.set_state(BookingFlow.master)
        if isinstance(callback.message, Message):
            data = await state.get_data()
            await callback.message.answer(
                "Выберите мастера или доверьте выбор нам:",
                reply_markup=masters_keyboard(
                    options.masters,
                    back_action=("back_addons" if data.get("addons_shown") else "back_services"),
                ),
            )
        await callback.answer()
        return
    selected_staff_id = options.masters[0].id if len(options.masters) == 1 else None
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


@router.callback_query(BookingCallback.filter(F.action == "back_addons"))
async def return_to_addons(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    data = await state.get_data()
    try:
        service_id = int(str(data["service_id"]))
        addons = await booking_service.list_service_addons(
            actor_from_telegram(callback.from_user), service_id
        )
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not addons:
        await callback.answer("Дополнительные услуги больше недоступны.", show_alert=True)
        return
    valid_ids = {addon.id for addon in addons}
    selected = {
        value
        for value in data.get("addon_ids", [])
        if isinstance(value, int) and value in valid_ids
    }
    await state.update_data(addon_ids=[addon.id for addon in addons if addon.id in selected])
    await state.set_state(BookingFlow.addons)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Выберите дополнительные услуги:",
            reply_markup=addons_keyboard(addons, selected),
        )
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
            addon_ids=_addon_ids(await state.get_data()),
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
    back_action = _selection_back_action(data)
    await state.set_state(BookingFlow.date)
    if isinstance(callback.message, Message):
        keyboard = dates_keyboard(dates, back_action=back_action)
        if callback.message.photo:
            await callback.message.answer("Выберите дату:", reply_markup=keyboard)
        else:
            await callback.message.edit_text("Выберите дату:", reply_markup=keyboard)
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
            reply_markup=masters_keyboard(
                options.masters,
                back_action=("back_addons" if data.get("addons_shown") else "back_services"),
            ),
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
            addon_ids=_addon_ids(data),
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
            addon_ids=_addon_ids(data),
            staff_member_id=staff_member_id,
        )
    except (DomainError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.set_state(BookingFlow.date)
    if isinstance(callback.message, Message):
        back_action = _selection_back_action(data)
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
            addon_ids=_addon_ids(data),
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


def _addon_ids(data: dict[str, object]) -> list[int]:
    raw = data.get("addon_ids", [])
    if not isinstance(raw, list):
        return []
    return [value for value in raw if isinstance(value, int) and value > 0]


def _selection_back_action(data: dict[str, object]) -> str:
    if data.get("master_selection_shown"):
        return "back_masters"
    if data.get("addons_shown"):
        return "back_addons"
    return "back_services"
