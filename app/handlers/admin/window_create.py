"""FSM for creating a concrete manual availability window."""

from __future__ import annotations

from datetime import date, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.enums import AvailabilityWindowStatus
from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram, parse_positive_minutes
from app.handlers.admin.window_common import parse_local_date, parse_local_time, render_window
from app.keyboards.admin.main import ADMIN_ADD_WINDOW_TEXT, admin_main_keyboard
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.admin.windows import (
    WindowCallback,
    window_details_keyboard,
    window_status_keyboard,
)
from app.schemas.availability import AvailabilityWindowCreate
from app.services.availability_service import AvailabilityService
from app.states.admin_window import AdminWindowCreate

router = Router(name="admin.window_create")


async def begin_window_creation_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminWindowCreate.local_date)
    await message.answer(
        "Введите дату окна в формате ДД.ММ.ГГГГ:",
        reply_markup=cancel_keyboard(),
    )


@router.message(F.text == ADMIN_ADD_WINDOW_TEXT)
async def begin_window_creation_from_menu(message: Message, state: FSMContext) -> None:
    await begin_window_creation_message(message, state)


@router.callback_query(WindowCallback.filter(F.action == "add"))
async def begin_window_creation_from_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if isinstance(callback.message, Message):
        await begin_window_creation_message(callback.message, state)
    await callback.answer()


@router.message(AdminWindowCreate.local_date)
async def capture_window_date(message: Message, state: FSMContext) -> None:
    local_date = parse_local_date(message.text)
    if local_date is None:
        await message.answer("Не удалось прочитать дату. Пример: 28.07.2026")
        return
    await state.update_data(local_date=local_date.isoformat())
    await state.set_state(AdminWindowCreate.local_time)
    await message.answer("Введите время начала в формате ЧЧ:ММ:")


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
