"""FSM for creating a concrete manual availability window."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
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
from app.keyboards.admin.windows import (
    WindowCallback,
    window_details_keyboard,
    window_status_keyboard,
)
from app.keyboards.common.date_picker import DatePickerCallback, date_picker_keyboard
from app.logging import log_event
from app.schemas.availability import AvailabilityWindowCreate
from app.services.availability_service import AvailabilityService
from app.services.date_picker_service import DatePickerPage, DatePickerService
from app.services.settings_service import SettingsService
from app.states.admin_window import AdminWindowCreate

router = Router(name="admin.window_create")
logger = logging.getLogger(__name__)
date_picker_service = DatePickerService()


async def begin_window_creation_message(
    message: Message,
    state: FSMContext,
    settings_service: SettingsService,
) -> None:
    await state.clear()
    if message.from_user is None:
        return
    await state.set_state(AdminWindowCreate.local_date)
    settings = await settings_service.get(actor_from_telegram(message.from_user))
    today = datetime.now(UTC).astimezone(ZoneInfo(settings.timezone)).date()
    page = date_picker_service.build_page(
        today=today,
        requested_start=today,
        booking_horizon_days=settings.booking_horizon_days,
        allow_saturday=settings.allow_saturday,
        allow_sunday=settings.allow_sunday,
    )
    await message.answer(
        _date_picker_text(page),
        reply_markup=date_picker_keyboard(page),
    )


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
        await state.clear()
        if isinstance(callback.message, Message):
            await callback.message.edit_text("Создание окна отменено.")
            await callback.message.answer("Главное меню:", reply_markup=admin_main_keyboard())
        await callback.answer()
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
    today = datetime.now(UTC).astimezone(ZoneInfo(settings.timezone)).date()
    try:
        selected = date.fromisoformat(callback_data.value)
        if callback_data.action == "page":
            page = date_picker_service.build_page(
                today=today,
                requested_start=selected,
                booking_horizon_days=settings.booking_horizon_days,
                allow_saturday=settings.allow_saturday,
                allow_sunday=settings.allow_sunday,
            )
            if isinstance(callback.message, Message):
                await callback.message.edit_text(
                    _date_picker_text(page),
                    reply_markup=date_picker_keyboard(page),
                )
            await callback.answer()
            return
        if callback_data.action != "pick":
            raise DatePickerValidationError("Неизвестная команда календаря.")
        local_date = date_picker_service.validate_selection(
            selected,
            today=today,
            booking_horizon_days=settings.booking_horizon_days,
            allow_saturday=settings.allow_saturday,
            allow_sunday=settings.allow_sunday,
        )
    except (DatePickerValidationError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(local_date=local_date.isoformat())
    await state.set_state(AdminWindowCreate.local_time)
    log_event(
        logger,
        logging.INFO,
        "availability_date_selected",
        local_date=local_date.isoformat(),
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(f"Выбрана дата: {local_date:%d.%m.%Y}.")
        await callback.message.answer("Введите время начала в формате ЧЧ:ММ:")
    await callback.answer()


@router.message(AdminWindowCreate.local_date)
async def reject_manual_window_date(message: Message) -> None:
    await message.answer("Выберите дату кнопкой в календаре выше.")


@router.message(AdminWindowCreate.local_time)
async def capture_window_time(message: Message, state: FSMContext) -> None:
    local_time = parse_local_time(message.text)
    if local_time is None:
        await message.answer("Не удалось прочитать время. Пример: 15:30")
        return
    await state.update_data(local_start_time=local_time.isoformat())
    await state.set_state(AdminWindowCreate.duration)
    await message.answer(
        "Введите продолжительность окна в минутах или «-», "
        "чтобы использовать настройку по умолчанию:"
    )


@router.message(AdminWindowCreate.duration)
async def capture_window_duration(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    duration = None if raw == "-" else parse_positive_minutes(raw)
    if raw != "-" and duration is None:
        await message.answer("Введите целое число минут от 1 до 1440 либо «-».")
        return
    await state.update_data(duration_minutes=duration)
    await state.set_state(AdminWindowCreate.comment)
    await message.answer("Введите внутренний комментарий или «-», если его нет:")


@router.message(AdminWindowCreate.comment)
async def capture_window_comment(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    comment = None if raw == "-" else raw
    if comment is not None and len(comment) > 2000:
        await message.answer("Комментарий не должен превышать 2000 символов.")
        return
    await state.update_data(admin_comment=comment)
    await state.set_state(AdminWindowCreate.status)
    await message.answer("В каком статусе создать окно?", reply_markup=window_status_keyboard())


@router.callback_query(
    AdminWindowCreate.status,
    WindowCallback.filter(F.action.in_({"status_open", "status_closed"})),
)
async def finish_window_creation(
    callback: CallbackQuery,
    callback_data: WindowCallback,
    state: FSMContext,
    availability_service: AvailabilityService,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    try:
        values = AvailabilityWindowCreate(
            local_date=date.fromisoformat(str(data["local_date"])),
            local_start_time=time.fromisoformat(str(data["local_start_time"])),
            duration_minutes=data.get("duration_minutes"),
            admin_comment=data.get("admin_comment"),
            status=(
                AvailabilityWindowStatus.OPEN
                if callback_data.action == "status_open"
                else AvailabilityWindowStatus.CLOSED
            ),
        )
        window = await availability_service.create_window(
            actor_from_telegram(callback.from_user),
            values,
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await state.clear()
        await callback.answer(str(exc), show_alert=True)
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Окно не создано. Начните ввод заново.",
                reply_markup=admin_main_keyboard(),
            )
        return

    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Окно создано.\n\n" + render_window(window),
            reply_markup=window_details_keyboard(window),
        )
        await callback.message.answer("Готово.", reply_markup=admin_main_keyboard())
    await callback.answer("Окно создано.")


def _date_picker_text(page: DatePickerPage) -> str:
    return (
        "Выберите дату нового окна.\n\n"
        f"Показаны даты с {page.start_date:%d.%m.%Y} по {page.end_date:%d.%m.%Y}."
    )
