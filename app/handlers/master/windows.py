"""Button-first, self-scoped free-window creation for masters."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.enums import AvailabilityWindowStatus
from app.domain.errors import DatePickerValidationError, DomainError
from app.handlers.admin.service_common import parse_positive_minutes
from app.handlers.admin.window_common import parse_local_time, render_window
from app.keyboards.common.date_picker import DatePickerCallback, date_picker_keyboard
from app.keyboards.common.time_picker import (
    TimePickerCallback,
    decode_clock_value,
    manual_time_keyboard,
    time_picker_keyboard,
)
from app.keyboards.master.windows import (
    MasterWindowFormCallback,
    master_window_confirmation_keyboard,
    master_window_created_keyboard,
    master_window_duration_keyboard,
)
from app.keyboards.master.workspace import MasterScheduleCallback
from app.schemas.authorization import StaffContext
from app.schemas.availability import AvailabilityWindowCreate, AvailabilityWindowPreview
from app.schemas.settings import BusinessSettingsView
from app.services.availability_service import AvailabilityService
from app.services.date_picker_service import DatePickerPage, DatePickerService
from app.states.master_window import MasterWindowCreate
from app.utils.telegram import edit_text_safely

router = Router(name="master.windows")
date_picker_service = DatePickerService()


@router.callback_query(MasterScheduleCallback.filter(F.action == "add_window"))
async def begin_own_window(
    callback: CallbackQuery,
    state: FSMContext,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
) -> None:
    await state.clear()
    settings = await availability_service.get_creation_settings(staff_context)
    await state.update_data(staff_member_id=staff_context.staff_member_id)
    await state.set_state(MasterWindowCreate.local_date)
    page = _date_page(settings)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите дату свободного окна. Услугу и рабочее место бот определит "
            "при записи клиента.",
            reply_markup=date_picker_keyboard(page),
        )
    await callback.answer()


@router.callback_query(MasterWindowCreate.local_date, DatePickerCallback.filter())
async def select_own_window_date(
    callback: CallbackQuery,
    callback_data: DatePickerCallback,
    state: FSMContext,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
) -> None:
    if callback_data.action == "cancel":
        await _cancel(callback, state)
        return
    if callback_data.action == "back":
        await _cancel(callback, state, text="Создание окна закрыто.")
        return
    if callback_data.action == "noop":
        await callback.answer("Других дат в этом направлении нет.")
        return
    settings = await availability_service.get_creation_settings(staff_context)
    today = _today(settings)
    try:
        selected = date.fromisoformat(callback_data.value)
        if callback_data.action == "page":
            page = _date_page(settings, requested_start=selected)
            if isinstance(callback.message, Message):
                await edit_text_safely(
                    callback.message,
                    "Выберите дату свободного окна:",
                    reply_markup=date_picker_keyboard(page),
                )
            await callback.answer()
            return
        if callback_data.action == "off":
            raise DatePickerValidationError("Этот день недоступен в настройках записи.")
        if callback_data.action != "pick":
            raise DatePickerValidationError("Календарь устарел. Откройте его заново.")
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
    await state.set_state(MasterWindowCreate.local_time)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"Дата: {selected:%d.%m.%Y}. Выберите время начала:",
            reply_markup=time_picker_keyboard(),
        )
    await callback.answer()


@router.callback_query(
    MasterWindowCreate.local_time,
    TimePickerCallback.filter(),
)
@router.callback_query(
    MasterWindowCreate.manual_time,
    TimePickerCallback.filter(),
)
async def select_own_window_time(
    callback: CallbackQuery,
    callback_data: TimePickerCallback,
    state: FSMContext,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
) -> None:
    if callback_data.action == "cancel":
        await _cancel(callback, state)
        return
    if callback_data.action == "date":
        settings = await availability_service.get_creation_settings(staff_context)
        await state.set_state(MasterWindowCreate.local_date)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Выберите дату свободного окна:",
                reply_markup=date_picker_keyboard(_date_page(settings)),
            )
        await callback.answer()
        return
    if callback_data.action == "manual":
        await state.set_state(MasterWindowCreate.manual_time)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Введите время в формате ЧЧ:ММ, например 09:30:",
                reply_markup=manual_time_keyboard(),
            )
        await callback.answer()
        return
    if callback_data.action == "back":
        await state.set_state(MasterWindowCreate.local_time)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Выберите время начала:",
                reply_markup=time_picker_keyboard(),
            )
        await callback.answer()
        return
    if callback_data.action != "pick":
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    selected = parse_local_time(decode_clock_value(callback_data.value))
    if selected is None:
        await callback.answer("Выберите корректное время.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await _accept_time(
            callback.message,
            state,
            selected,
            staff_context,
            availability_service,
        )
    await callback.answer()


@router.message(MasterWindowCreate.manual_time)
async def capture_own_window_time(
    message: Message,
    state: FSMContext,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
) -> None:
    selected = parse_local_time(message.text)
    if selected is None:
        await message.answer("Введите время в формате ЧЧ:ММ, например 09:30.")
        return
    await _accept_time(message, state, selected, staff_context, availability_service)


@router.callback_query(MasterWindowFormCallback.filter())
async def handle_own_window_form(
    callback: CallbackQuery,
    callback_data: MasterWindowFormCallback,
    state: FSMContext,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
    correlation_id: str,
) -> None:
    action = callback_data.action
    if action in {"cancel", "done"}:
        await _cancel(callback, state, text="Готово." if action == "done" else "Создание отменено.")
        return
    if action == "another":
        settings = await availability_service.get_creation_settings(staff_context)
        await state.clear()
        await state.update_data(staff_member_id=staff_context.staff_member_id)
        await state.set_state(MasterWindowCreate.local_date)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Выберите дату следующего свободного окна:",
                reply_markup=date_picker_keyboard(_date_page(settings)),
            )
        await callback.answer()
        return
    if action == "duration_manual":
        await state.set_state(MasterWindowCreate.manual_duration)
        if isinstance(callback.message, Message):
            await callback.message.edit_text("Введите длительность окна в минутах (1–1440):")
        await callback.answer()
        return
    if action == "duration_default":
        await state.update_data(duration_minutes=None)
        await _show_confirmation(callback, state, staff_context, availability_service)
        return
    if action == "edit_date":
        settings = await availability_service.get_creation_settings(staff_context)
        await state.set_state(MasterWindowCreate.local_date)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Выберите новую дату:",
                reply_markup=date_picker_keyboard(_date_page(settings)),
            )
        await callback.answer()
        return
    if action == "edit_time":
        await state.set_state(MasterWindowCreate.local_time)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Выберите новое время:", reply_markup=time_picker_keyboard()
            )
        await callback.answer()
        return
    if action == "edit_duration":
        settings = await availability_service.get_creation_settings(staff_context)
        await state.set_state(MasterWindowCreate.duration)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Выберите длительность:",
                reply_markup=master_window_duration_keyboard(
                    settings.default_window_duration_minutes
                ),
            )
        await callback.answer()
        return
    if action == "create" and await state.get_state() == MasterWindowCreate.confirm.state:
        try:
            created = await availability_service.create_window(
                staff_context,
                _values(await state.get_data(), staff_context),
                correlation_id=correlation_id,
            )
        except (DomainError, ValidationError, KeyError, ValueError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await state.clear()
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                "Свободное окно открыто.\n\n" + render_window(created),
                reply_markup=master_window_created_keyboard(),
            )
        await callback.answer("Окно открыто.")
        return
    await callback.answer("Кнопка устарела.", show_alert=True)


@router.message(MasterWindowCreate.manual_duration)
async def capture_own_window_duration(
    message: Message,
    state: FSMContext,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
) -> None:
    duration = parse_positive_minutes(message.text)
    if duration is None or duration > 1440:
        await message.answer("Введите целое число минут от 1 до 1440.")
        return
    await state.update_data(duration_minutes=duration)
    await _show_confirmation_message(message, state, staff_context, availability_service)


async def _accept_time(
    message: Message | None,
    state: FSMContext,
    selected: time,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
) -> None:
    await state.update_data(local_start_time=selected.isoformat())
    settings = await availability_service.get_creation_settings(staff_context)
    await state.set_state(MasterWindowCreate.duration)
    if isinstance(message, Message):
        await message.answer(
            f"Начало: {selected:%H:%M}. Выберите длительность:",
            reply_markup=master_window_duration_keyboard(settings.default_window_duration_minutes),
        )


async def _show_confirmation(
    callback: CallbackQuery,
    state: FSMContext,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    try:
        preview = await availability_service.preview_window(
            staff_context,
            _values(await state.get_data(), staff_context),
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.set_state(MasterWindowCreate.confirm)
    await callback.message.edit_text(
        _render_preview(preview),
        reply_markup=master_window_confirmation_keyboard(),
    )
    await callback.answer()


async def _show_confirmation_message(
    message: Message,
    state: FSMContext,
    staff_context: StaffContext,
    availability_service: AvailabilityService,
) -> None:
    try:
        preview = await availability_service.preview_window(
            staff_context,
            _values(await state.get_data(), staff_context),
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.set_state(MasterWindowCreate.confirm)
    await message.answer(
        _render_preview(preview),
        reply_markup=master_window_confirmation_keyboard(),
    )


async def _cancel(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    text: str = "Создание окна отменено.",
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text)
    await callback.answer()


def _values(data: dict[str, object], context: StaffContext) -> AvailabilityWindowCreate:
    return AvailabilityWindowCreate(
        local_date=date.fromisoformat(str(data["local_date"])),
        local_start_time=time.fromisoformat(str(data["local_start_time"])),
        staff_member_id=context.staff_member_id,
        duration_minutes=data.get("duration_minutes"),
        status=AvailabilityWindowStatus.OPEN,
    )


def _date_page(
    settings: BusinessSettingsView,
    *,
    requested_start: date | None = None,
) -> DatePickerPage:
    today = _today(settings)
    return date_picker_service.build_page(
        today=today,
        requested_start=requested_start or today,
        booking_horizon_days=settings.booking_horizon_days,
        allow_saturday=settings.allow_saturday,
        allow_sunday=settings.allow_sunday,
        page_size=settings.availability_date_picker_days,
    )


def _today(settings: BusinessSettingsView) -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(settings.timezone)).date()


def _render_preview(preview: AvailabilityWindowPreview) -> str:
    zone = ZoneInfo(preview.timezone)
    start = preview.start_at.astimezone(zone)
    end = preview.end_at.astimezone(zone)
    return (
        "<b>Проверьте свободное окно</b>\n\n"
        f"Мастер: {escape(preview.master_name)}\n"
        f"Дата: {start:%d.%m.%Y}\n"
        f"Время: {start:%H:%M}–{end:%H:%M}\n"
        f"Длительность: {preview.duration_minutes} мин.\n\n"
        "Услугу и рабочее место бот проверит при записи клиента."
    )
