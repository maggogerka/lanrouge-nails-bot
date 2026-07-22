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
)
from app.keyboards.admin.windows import WindowCallback
from app.keyboards.common.date_picker import DatePickerCallback, date_picker_keyboard
from app.keyboards.common.time_picker import (
    TimePickerCallback,
    decode_clock_value,
    manual_time_keyboard,
    time_picker_keyboard,
)
from app.logging import log_event
from app.schemas.availability import (
    AvailabilityWindowCreate,
    AvailabilityWindowPreview,
)
from app.schemas.service import AdminActor
from app.schemas.settings import BusinessSettingsView
from app.services.availability_service import AvailabilityService
from app.services.date_picker_service import DatePickerPage, DatePickerService
from app.services.settings_service import SettingsService
from app.states.admin_window import AdminWindowCreate

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
) -> None:
    await state.clear()
    if message.from_user is None:
        return
    settings = await settings_service.get(actor_from_telegram(message.from_user))
    page = _build_date_page(settings)
    await state.set_state(AdminWindowCreate.local_date)
    await message.answer(_date_picker_text(page), reply_markup=date_picker_keyboard(page))


@router.message(F.text == ADMIN_ADD_WINDOW_TEXT)
async def begin_window_creation_from_menu(
    message: Message,
    state: FSMContext,
    settings_service: SettingsService,
) -> None:
    await begin_window_creation_message(message, state, settings_service)


@router.callback_query(WindowCallback.filter(F.action == "add"))
async def begin_window_creation_from_callback(
    callback: CallbackQuery,
    state: FSMContext,
    settings_service: SettingsService,
) -> None:
    if isinstance(callback.message, Message):
        await begin_window_creation_message(callback.message, state, settings_service)
    await callback.answer()


@router.callback_query(DatePickerCallback.filter())
async def handle_date_picker(
    callback: CallbackQuery,
    callback_data: DatePickerCallback,
    state: FSMContext,
    settings_service: SettingsService,
    availability_service: AvailabilityService,
) -> None:
    if callback_data.action == "cancel":
        await _cancel_creation(callback, state)
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
            if isinstance(callback.message, Message):
                await callback.message.edit_text(
                    _date_picker_text(page),
                    reply_markup=date_picker_keyboard(page),
                )
            await callback.answer()
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
) -> None:
    if callback_data.action == "cancel":
        await _cancel_creation(callback, state)
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
) -> None:
    action = callback_data.action
    if action == "cancel":
        await _cancel_creation(callback, state)
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
        await _finish_creation(callback, state)
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
        selected_date = _state_date(await state.get_data())
        await state.set_data({"local_date": selected_date.isoformat()})
        await state.set_state(AdminWindowCreate.local_time)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                _time_picker_text(selected_date),
                reply_markup=time_picker_keyboard(),
            )
        await callback.answer()
        return
    if action == "another_date" and current_state == AdminWindowCreate.completed.state:
        await state.set_data({})
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


async def _cancel_creation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Создание окна отменено.")
        await callback.message.answer("Главное меню:", reply_markup=admin_main_keyboard())
    await callback.answer()


async def _finish_creation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Готово.", reply_markup=admin_main_keyboard())
    await callback.answer()


async def _stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка устарела. Начните создание окна заново.", show_alert=True)


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
        duration_minutes=data.get("duration_minutes"),
        admin_comment=data.get("admin_comment"),
        status=AvailabilityWindowStatus.OPEN,
    )


def _state_date(data: dict[str, object]) -> date:
    return date.fromisoformat(str(data["local_date"]))


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
