"""Button-first FSM for creating a concrete availability window."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.enums import AvailabilityWindowStatus
from app.domain.errors import DatePickerValidationError, DomainError
from app.domain.tenancy import DEFAULT_STAFF_MEMBER_ID
from app.handlers.admin.service_common import actor_from_telegram, parse_positive_minutes
from app.handlers.admin.window_browse import show_windows_callback
from app.handlers.admin.window_common import parse_local_time, render_window
from app.keyboards.admin.main import ADMIN_ADD_WINDOW_TEXT, admin_main_keyboard
from app.keyboards.admin.window_create import (
    WindowFormCallback,
    comment_keyboard,
    duration_keyboard,
    window_confirmation_keyboard,
    window_created_keyboard,
    window_master_keyboard,
    window_service_keyboard,
)
from app.keyboards.admin.windows import WindowCallback, stale_window_keyboard
from app.keyboards.common.date_picker import DatePickerCallback, date_picker_keyboard
from app.keyboards.common.time_picker import (
    TimePickerCallback,
    decode_clock_value,
    manual_time_keyboard,
    time_picker_keyboard,
)
from app.logging import log_event
from app.schemas.authorization import StaffContext
from app.schemas.availability import (
    AvailabilityWindowCreate,
    AvailabilityWindowPreview,
)
from app.schemas.service import AdminActor
from app.schemas.settings import BusinessSettingsView
from app.services.authorization_service import AuthorizationService
from app.services.availability_service import AvailabilityService
from app.services.date_picker_service import DatePickerPage, DatePickerService
from app.services.menu_service import MenuService
from app.services.settings_service import SettingsService
from app.states.admin_window import AdminWindowCreate
from app.utils.telegram import edit_text_safely

router = Router(name="admin.window_create")
logger = logging.getLogger(__name__)
date_picker_service = DatePickerService()

_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


async def begin_window_creation_message(
    message: Message,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
    *,
    actor: AdminActor | None = None,
    staff_context: StaffContext | None = None,
    authorization_service: AuthorizationService | None = None,
) -> None:
    await state.clear()
    if actor is None:
        if message.from_user is None:
            return
        actor = actor_from_telegram(message.from_user)
    if staff_context is not None and authorization_service is not None:
        members = await authorization_service.list_staff(staff_context)
        masters = tuple(member for member in members if member.is_active and member.is_bookable)
        if not masters:
            await message.answer(
                "Сначала откройте «Мастера и сотрудники» и включите "
                "«Принимать записи» хотя бы в одном профиле."
            )
            return
        if len(masters) > 1:
            await state.set_state(AdminWindowCreate.master)
            await message.answer(
                "Для какого мастера создать окно?",
                reply_markup=window_master_keyboard(masters),
            )
            return
        await state.update_data(staff_member_id=masters[0].id)
        await _show_service_picker(
            message,
            state,
            availability_service,
            actor,
            masters[0].id,
        )
        return
    await message.answer("Не удалось определить мастера. Откройте создание окна заново.")


@router.message(F.text == ADMIN_ADD_WINDOW_TEXT)
async def begin_window_creation_from_menu(
    message: Message,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
    authorization_service: AuthorizationService | None = None,
    staff_context: StaffContext | None = None,
) -> None:
    await begin_window_creation_message(
        message,
        state,
        settings_service,
        availability_service,
        staff_context=staff_context,
        authorization_service=authorization_service,
    )


@router.callback_query(WindowCallback.filter(F.action == "add"))
async def begin_window_creation_from_callback(
    callback: CallbackQuery,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
    authorization_service: AuthorizationService | None = None,
    staff_context: StaffContext | None = None,
) -> None:
    if isinstance(callback.message, Message):
        await begin_window_creation_message(
            callback.message,
            state,
            settings_service,
            availability_service,
            actor=actor_from_telegram(callback.from_user),
            staff_context=staff_context,
            authorization_service=authorization_service,
        )
    await callback.answer()


@router.callback_query(AdminWindowCreate.master, WindowFormCallback.filter(F.action == "master"))
async def select_window_master(
    callback: CallbackQuery,
    callback_data: WindowFormCallback,
    state: FSMContext,
    availability_service: AvailabilityService,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
) -> None:
    try:
        staff_member_id = int(callback_data.value)
        members = await authorization_service.list_staff(staff_context)
        selected = next(
            member
            for member in members
            if member.id == staff_member_id and member.is_active and member.is_bookable
        )
    except (DomainError, ValueError, StopIteration):
        await callback.answer("Мастер больше недоступен.", show_alert=True)
        return
    await state.update_data(staff_member_id=selected.id)
    services = await availability_service.list_services_for_staff(
        actor_from_telegram(callback.from_user),
        selected.id,
    )
    if not services:
        await callback.answer(
            "У мастера нет услуг для онлайн-записи. Сначала назначьте услуги в его карточке.",
            show_alert=True,
        )
        return
    await state.set_state(AdminWindowCreate.service)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"Мастер: <b>{escape(selected.display_name)}</b>\n\nВыберите услугу для этого окна:",
            reply_markup=window_service_keyboard(services),
        )
    await callback.answer()


@router.callback_query(
    AdminWindowCreate.service,
    WindowFormCallback.filter(F.action == "service"),
)
async def select_window_service(
    callback: CallbackQuery,
    callback_data: WindowFormCallback,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
) -> None:
    data = await state.get_data()
    try:
        staff_member_id = int(str(data["staff_member_id"]))
        service_id = int(callback_data.value)
        services = await availability_service.list_services_for_staff(
            actor_from_telegram(callback.from_user),
            staff_member_id,
        )
        selected = next(service for service in services if service.id == service_id)
    except (DomainError, KeyError, ValueError, StopIteration):
        await callback.answer("Услуга больше недоступна этому мастеру.", show_alert=True)
        return
    await state.update_data(
        service_id=selected.id,
        service_name=selected.name,
        service_duration_max_minutes=selected.duration_max_minutes,
    )
    settings = await settings_service.get(actor_from_telegram(callback.from_user))
    await state.set_state(AdminWindowCreate.local_date)
    page = _build_date_page(settings)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"Услуга: <b>{escape(selected.name)}</b>\n\n" + _date_picker_text(page),
            reply_markup=date_picker_keyboard(page),
        )
    await callback.answer()


@router.callback_query(DatePickerCallback.filter())
async def handle_date_picker(
    callback: CallbackQuery,
    callback_data: DatePickerCallback,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
    menu_service: MenuService,
) -> None:
    if callback_data.action == "cancel":
        await _cancel_creation(callback, state, menu_service)
        return
    if callback_data.action == "back":
        await state.clear()
        await show_windows_callback(
            callback,
            availability_service,
            actor_from_telegram(callback.from_user),
        )
        return
    if callback_data.action == "noop":
        await callback.answer("Других дат в этом направлении нет.")
        return
    if callback_data.action == "off":
        await callback.answer(
            "Этот выходной сейчас недоступен для создания открытого окна.",
            show_alert=True,
        )
        return
    if await state.get_state() != AdminWindowCreate.local_date.state:
        await callback.answer(
            "Этот календарь устарел. Начните создание окна заново.",
            show_alert=True,
        )
        return

    settings = await settings_service.get(actor_from_telegram(callback.from_user))
    today = _business_today(settings)
    try:
        selected = date.fromisoformat(callback_data.value)
        if callback_data.action == "page":
            page = _build_date_page(settings, requested_start=selected)
            changed = True
            if isinstance(callback.message, Message):
                changed = await edit_text_safely(
                    callback.message,
                    _date_picker_text(page),
                    reply_markup=date_picker_keyboard(page),
                )
            await callback.answer(None if changed else "Показаны актуальные даты.")
            return
        if callback_data.action != "pick":
            raise DatePickerValidationError("Неизвестная команда календаря.")
        selected = date_picker_service.validate_selection(
            selected,
            today=today,
            booking_horizon_days=settings.booking_horizon_days,
            allow_saturday=settings.allow_saturday,
            allow_sunday=settings.allow_sunday,
        )
    except (DatePickerValidationError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(local_date=selected.isoformat())
    await state.set_state(AdminWindowCreate.local_time)
    log_event(
        logger,
        logging.INFO,
        "availability_date_selected",
        local_date=selected.isoformat(),
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _time_picker_text(selected),
            reply_markup=time_picker_keyboard(),
        )
    await callback.answer()


@router.message(AdminWindowCreate.local_date)
async def reject_manual_window_date(message: Message) -> None:
    await message.answer("Выберите дату кнопкой в календаре выше.")


@router.callback_query(TimePickerCallback.filter())
async def handle_time_picker(
    callback: CallbackQuery,
    callback_data: TimePickerCallback,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
    menu_service: MenuService,
) -> None:
    if callback_data.action == "cancel":
        await _cancel_creation(callback, state, menu_service)
        return
    current_state = await state.get_state()
    if callback_data.action == "date":
        if current_state not in {
            AdminWindowCreate.local_time.state,
            AdminWindowCreate.manual_time.state,
        }:
            await _stale_callback(callback)
            return
        settings = await settings_service.get(actor_from_telegram(callback.from_user))
        await state.set_state(AdminWindowCreate.local_date)
        page = _build_date_page(settings)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                _date_picker_text(page),
                reply_markup=date_picker_keyboard(page),
            )
        await callback.answer()
        return
    if callback_data.action == "manual":
        if current_state != AdminWindowCreate.local_time.state:
            await _stale_callback(callback)
            return
        await state.set_state(AdminWindowCreate.manual_time)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Введите время в формате ЧЧ:ММ, например 09:30 или 14:15.",
                reply_markup=manual_time_keyboard(),
            )
        await callback.answer()
        return
    if callback_data.action == "back":
        if current_state != AdminWindowCreate.manual_time.state:
            await _stale_callback(callback)
            return
        await state.set_state(AdminWindowCreate.local_time)
        selected_date = _state_date(await state.get_data())
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                _time_picker_text(selected_date),
                reply_markup=time_picker_keyboard(),
            )
        await callback.answer()
        return
    if callback_data.action != "pick" or current_state != AdminWindowCreate.local_time.state:
        await _stale_callback(callback)
        return

    selected_time = parse_local_time(decode_clock_value(callback_data.value))
    if selected_time is None:
        await callback.answer("Это время недоступно. Выберите другое.", show_alert=True)
        return
    settings = await settings_service.get(actor_from_telegram(callback.from_user))
    try:
        await _preview_selected_time(
            state,
            selected_time,
            actor_from_telegram(callback.from_user),
            availability_service,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(_time_error(exc), show_alert=True)
        return
    await _accept_selected_time(state, selected_time)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(f"Выбрано время: {selected_time:%H:%M}.")
        await callback.message.answer(
            "Выберите продолжительность окна:",
            reply_markup=duration_keyboard(settings.default_window_duration_minutes),
        )
    await callback.answer()


@router.message(AdminWindowCreate.local_time)
async def reject_time_without_manual_action(message: Message) -> None:
    await message.answer("Выберите время кнопкой или нажмите «Ввести другое время».")


@router.message(AdminWindowCreate.manual_time)
async def capture_manual_window_time(
    message: Message,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
) -> None:
    selected_time = parse_local_time(message.text)
    if selected_time is None:
        await message.answer(
            "Введите время в формате ЧЧ:ММ, например 09:30 или 14:15.",
            reply_markup=manual_time_keyboard(),
        )
        return
    if message.from_user is None:
        return
    settings = await settings_service.get(actor_from_telegram(message.from_user))
    try:
        await _preview_selected_time(
            state,
            selected_time,
            actor_from_telegram(message.from_user),
            availability_service,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await state.set_state(AdminWindowCreate.local_time)
        await message.answer(
            _time_error(exc) + "\n\nВыберите другое время:",
            reply_markup=time_picker_keyboard(),
        )
        return
    await _accept_selected_time(state, selected_time)
    await message.answer(
        f"Выбрано время: {selected_time:%H:%M}.\n\nВыберите продолжительность окна:",
        reply_markup=duration_keyboard(settings.default_window_duration_minutes),
    )


@router.callback_query(WindowFormCallback.filter())
async def handle_window_form_action(
    callback: CallbackQuery,
    callback_data: WindowFormCallback,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
    correlation_id: str,
    menu_service: MenuService,
) -> None:
    action = callback_data.action
    if action == "cancel":
        await _cancel_creation(callback, state, menu_service)
        return
    if action == "list":
        await state.clear()
        await show_windows_callback(
            callback,
            availability_service,
            actor_from_telegram(callback.from_user),
        )
        return
    if action == "done":
        await _finish_creation(callback, state, menu_service)
        return

    current_state = await state.get_state()
    settings = await settings_service.get(actor_from_telegram(callback.from_user))
    if action == "duration_default" and current_state == AdminWindowCreate.duration.state:
        await state.update_data(duration_minutes=None)
        await _show_comment_picker(callback, state)
        return
    if action == "duration_manual" and current_state == AdminWindowCreate.duration.state:
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Введите продолжительность окна в минутах от 1 до 1440."
            )
        await callback.answer()
        return
    if action == "comment_skip" and current_state == AdminWindowCreate.comment.state:
        await state.update_data(admin_comment=None)
        await _show_confirmation_callback(
            callback,
            state,
            availability_service,
        )
        return
    if action == "comment_manual" and current_state == AdminWindowCreate.comment.state:
        if isinstance(callback.message, Message):
            await callback.message.edit_text("Введите внутренний комментарий до 2000 символов.")
        await callback.answer()
        return
    if action == "edit_date" and current_state == AdminWindowCreate.confirm.state:
        await state.set_state(AdminWindowCreate.local_date)
        page = _build_date_page(settings)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                _date_picker_text(page),
                reply_markup=date_picker_keyboard(page),
            )
        await callback.answer()
        return
    if action == "edit_time" and current_state == AdminWindowCreate.confirm.state:
        await state.set_state(AdminWindowCreate.local_time)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                _time_picker_text(_state_date(await state.get_data())),
                reply_markup=time_picker_keyboard(),
            )
        await callback.answer()
        return
    if action == "edit_duration" and current_state == AdminWindowCreate.confirm.state:
        await state.set_state(AdminWindowCreate.duration)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Выберите продолжительность окна:",
                reply_markup=duration_keyboard(settings.default_window_duration_minutes),
            )
        await callback.answer()
        return
    if action == "edit_comment" and current_state == AdminWindowCreate.confirm.state:
        await _show_comment_picker(callback, state)
        return
    if action == "create" and current_state == AdminWindowCreate.confirm.state:
        await _create_confirmed_window(
            callback,
            state,
            availability_service,
            correlation_id,
        )
        return
    if action == "another_same" and current_state == AdminWindowCreate.completed.state:
        data = await state.get_data()
        selected_date = _state_date(data)
        await state.set_data(_repeat_window_data(data, local_date=selected_date))
        await state.set_state(AdminWindowCreate.local_time)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                _time_picker_text(selected_date),
                reply_markup=time_picker_keyboard(),
            )
        await callback.answer()
        return
    if action == "another_date" and current_state == AdminWindowCreate.completed.state:
        data = await state.get_data()
        await state.set_data(_repeat_window_data(data))
        await state.set_state(AdminWindowCreate.local_date)
        page = _build_date_page(settings)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                _date_picker_text(page),
                reply_markup=date_picker_keyboard(page),
            )
        await callback.answer()
        return
    await _stale_callback(callback)


@router.message(AdminWindowCreate.duration)
async def capture_window_duration(message: Message, state: FSMContext) -> None:
    duration = parse_positive_minutes(message.text)
    if duration is None:
        await message.answer("Введите целое число минут от 1 до 1440.")
        return
    await state.update_data(duration_minutes=duration)
    await state.set_state(AdminWindowCreate.comment)
    await message.answer(
        "Добавьте необязательный внутренний комментарий:",
        reply_markup=comment_keyboard(),
    )


@router.message(AdminWindowCreate.comment)
async def capture_window_comment(
    message: Message,
    state: FSMContext,
    availability_service: AvailabilityService,
) -> None:
    raw = (message.text or "").strip()
    if not raw or len(raw) > 2000:
        await message.answer("Комментарий должен содержать от 1 до 2000 символов.")
        return
    if message.from_user is None:
        return
    await state.update_data(admin_comment=raw)
    try:
        preview = await availability_service.preview_window(
            actor_from_telegram(message.from_user),
            _window_values(await state.get_data()),
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await state.set_state(AdminWindowCreate.local_time)
        await message.answer(
            _time_error(exc) + "\n\nВыберите другое время:",
            reply_markup=time_picker_keyboard(),
        )
        return
    await state.set_state(AdminWindowCreate.confirm)
    await message.answer(
        _render_confirmation(preview),
        reply_markup=window_confirmation_keyboard(),
    )


async def _preview_selected_time(
    state: FSMContext,
    selected_time: time,
    actor: AdminActor,
    availability_service: AvailabilityService,
) -> AvailabilityWindowPreview:
    data = await state.get_data()
    return await availability_service.preview_window(
        actor,
        AvailabilityWindowCreate(
            local_date=_state_date(data),
            local_start_time=selected_time,
            service_id=int(str(data["service_id"])),
            staff_member_id=int(str(data.get("staff_member_id", DEFAULT_STAFF_MEMBER_ID))),
            duration_minutes=int(str(data["service_duration_max_minutes"])),
            status=AvailabilityWindowStatus.OPEN,
        ),
    )


async def _accept_selected_time(state: FSMContext, selected_time: time) -> None:
    await state.update_data(local_start_time=selected_time.isoformat())
    await state.set_state(AdminWindowCreate.duration)
    log_event(
        logger,
        logging.INFO,
        "availability_time_selected",
        local_time=selected_time.strftime("%H:%M"),
    )


async def _show_comment_picker(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminWindowCreate.comment)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Добавьте необязательный внутренний комментарий:",
            reply_markup=comment_keyboard(),
        )
    await callback.answer()


async def _show_confirmation_callback(
    callback: CallbackQuery,
    state: FSMContext,
    availability_service: AvailabilityService,
) -> None:
    try:
        preview = await availability_service.preview_window(
            actor_from_telegram(callback.from_user),
            _window_values(await state.get_data()),
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await state.set_state(AdminWindowCreate.local_time)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                _time_error(exc) + "\n\nВыберите другое время:",
                reply_markup=time_picker_keyboard(),
            )
        await callback.answer()
        return
    await state.set_state(AdminWindowCreate.confirm)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_confirmation(preview),
            reply_markup=window_confirmation_keyboard(),
        )
    await callback.answer()


async def _create_confirmed_window(
    callback: CallbackQuery,
    state: FSMContext,
    availability_service: AvailabilityService,
    correlation_id: str,
) -> None:
    try:
        window = await availability_service.create_window(
            actor_from_telegram(callback.from_user),
            _window_values(await state.get_data()),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.set_state(AdminWindowCreate.completed)
    log_event(
        logger,
        logging.INFO,
        "availability_window_created",
        window_id=window.id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Окно успешно создано.\n\n" + render_window(window),
            reply_markup=window_created_keyboard(),
        )
    await callback.answer("Окно создано.")


async def _cancel_creation(
    callback: CallbackQuery, state: FSMContext, menu_service: MenuService
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Создание окна отменено.")
        await callback.message.answer(
            "Главное меню:",
            reply_markup=admin_main_keyboard(await menu_service.get_capabilities()),
        )
    await callback.answer()


async def _finish_creation(
    callback: CallbackQuery, state: FSMContext, menu_service: MenuService
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Готово.",
            reply_markup=admin_main_keyboard(await menu_service.get_capabilities()),
        )
    await callback.answer()


async def _stale_callback(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Черновик уже закрыт. Вернитесь к открытым окнам или начните создание заново.",
            reply_markup=stale_window_keyboard(),
        )
    await callback.answer("Эта кнопка устарела.", show_alert=True)


def _build_date_page(
    settings: BusinessSettingsView,
    *,
    requested_start: date | None = None,
) -> DatePickerPage:
    today = _business_today(settings)
    return date_picker_service.build_page(
        today=today,
        requested_start=requested_start or today,
        booking_horizon_days=settings.booking_horizon_days,
        allow_saturday=settings.allow_saturday,
        allow_sunday=settings.allow_sunday,
        page_size=settings.availability_date_picker_days,
    )


def _business_today(settings: BusinessSettingsView) -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(settings.timezone)).date()


def _window_values(data: dict[str, object]) -> AvailabilityWindowCreate:
    return AvailabilityWindowCreate(
        local_date=_state_date(data),
        local_start_time=time.fromisoformat(str(data["local_start_time"])),
        service_id=int(str(data["service_id"])),
        staff_member_id=int(str(data.get("staff_member_id", DEFAULT_STAFF_MEMBER_ID))),
        duration_minutes=data.get("duration_minutes"),
        admin_comment=data.get("admin_comment"),
        status=AvailabilityWindowStatus.OPEN,
    )


async def _show_date_picker(
    message: Message, state: FSMContext, settings: BusinessSettingsView
) -> None:
    page = _build_date_page(settings)
    await state.set_state(AdminWindowCreate.local_date)
    await message.answer(_date_picker_text(page), reply_markup=date_picker_keyboard(page))


async def _show_service_picker(
    message: Message,
    state: FSMContext,
    availability_service: AvailabilityService,
    actor: AdminActor,
    staff_member_id: int,
) -> None:
    services = await availability_service.list_services_for_staff(actor, staff_member_id)
    if not services:
        await state.clear()
        await message.answer(
            "У мастера нет услуг для онлайн-записи. Сначала назначьте услуги в разделе "
            "«Мастера и сотрудники»."
        )
        return
    await state.set_state(AdminWindowCreate.service)
    await message.answer(
        "Выберите услугу для нового окна:",
        reply_markup=window_service_keyboard(services),
    )


def _state_date(data: dict[str, object]) -> date:
    return date.fromisoformat(str(data["local_date"]))


def _repeat_window_data(
    data: dict[str, object],
    *,
    local_date: date | None = None,
) -> dict[str, object]:
    result = {
        key: data[key]
        for key in (
            "staff_member_id",
            "service_id",
            "service_name",
            "service_duration_max_minutes",
        )
        if key in data
    }
    if local_date is not None:
        result["local_date"] = local_date.isoformat()
    return result


def _date_picker_text(page: DatePickerPage) -> str:
    return (
        "Выберите дату нового окна.\n\n"
        f"Показаны даты с {page.start_date:%d.%m.%Y} по {page.end_date:%d.%m.%Y}."
    )


def _time_picker_text(selected_date: date) -> str:
    return f"Дата: {selected_date:%d.%m.%Y}.\n\nВыберите время начала:"


def _time_error(exc: Exception) -> str:
    text = str(exc)
    if "пересека" in text.casefold() or "между окнами" in text.casefold():
        return "Это время пересекается с другим окном или записью. Выберите другое."
    return text


def _render_confirmation(preview: AvailabilityWindowPreview) -> str:
    zone = ZoneInfo(preview.timezone)
    start = preview.start_at.astimezone(zone)
    end = preview.end_at.astimezone(zone)
    comment = escape(preview.admin_comment) if preview.admin_comment else "—"
    return (
        "<b>Новое открытое окно</b>\n\n"
        f"Услуга: <b>{escape(preview.service_name)}</b>\n"
        f"Рабочее место: {escape(preview.workstation_name)}\n"
        f"Дата: {start.day} {_MONTHS[start.month - 1]} {start.year}, "
        f"{_WEEKDAYS[start.weekday()]}\n"
        f"Время: {start:%H:%M}\n"
        f"Продолжительность: {_duration_text(preview.duration_minutes)}\n"
        f"Окончание: {end:%H:%M}\n"
        f"Комментарий: {comment}"
    )


def _duration_text(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours} ч {remainder} мин"
    if hours:
        return f"{hours} ч"
    return f"{remainder} мин"
