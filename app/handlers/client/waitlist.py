"""Client waitlist creation, browsing, cancellation and offer acceptance."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.errors import DomainError
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.booking import booking_navigation_keyboard
from app.keyboards.client.main import CLIENT_WAITLIST_TEXT
from app.keyboards.client.waitlist import (
    WaitlistCallback,
    waitlist_menu_keyboard,
    waitlist_services_keyboard,
    waitlist_time_keyboard,
)
from app.schemas.waitlist import WaitlistCreate
from app.services.booking_service import BookingService
from app.services.waitlist_service import WaitlistService
from app.states.booking import BookingFlow
from app.states.waitlist import WaitlistFlow

router = Router(name="client.waitlist")


def _parse_date(value: str) -> date:
    return (
        date.fromisoformat(value) if "-" in value else datetime.strptime(value, "%d.%m.%Y").date()
    )


@router.message(F.text == CLIENT_WAITLIST_TEXT)
async def show_waitlist(message: Message, waitlist_service: WaitlistService) -> None:
    if message.from_user is None:
        return
    page = await waitlist_service.list_my(actor_from_telegram(message.from_user))
    lines = [
        f"#{item.id} · {item.service_name} · "
        f"{item.date_from:%d.%m}–{item.date_to:%d.%m} · {item.status.value}"
        for item in page.items
    ]
    await message.answer(
        "Ваш лист ожидания:\n" + ("\n".join(lines) if lines else "Пока нет запросов."),
        reply_markup=waitlist_menu_keyboard(page.items),
    )


@router.callback_query(WaitlistCallback.filter(F.action == "add"))
async def begin_waitlist(
    callback: CallbackQuery,
    booking_service: BookingService,
    state: FSMContext,
) -> None:
    services = await booking_service.list_active_services(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Для какой услуги искать освободившееся время?",
            reply_markup=waitlist_services_keyboard(services),
        )
    await state.clear()
    await callback.answer()


@router.callback_query(WaitlistCallback.filter(F.action == "service"))
async def choose_waitlist_service(
    callback: CallbackQuery, callback_data: WaitlistCallback, state: FSMContext
) -> None:
    await state.update_data(service_id=callback_data.object_id)
    await state.set_state(WaitlistFlow.date_from)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите первую подходящую дату в формате ДД.ММ.ГГГГ:")
    await callback.answer()


@router.message(WaitlistFlow.date_from)
async def waitlist_date_from(message: Message, state: FSMContext) -> None:
    try:
        value = _parse_date((message.text or "").strip())
    except ValueError:
        await message.answer("Не удалось прочитать дату. Используйте формат ДД.ММ.ГГГГ.")
        return
    await state.update_data(date_from=value.isoformat())
    await state.set_state(WaitlistFlow.date_to)
    await message.answer("Введите последнюю подходящую дату в формате ДД.ММ.ГГГГ:")


@router.message(WaitlistFlow.date_to)
async def waitlist_date_to(message: Message, state: FSMContext) -> None:
    try:
        value = _parse_date((message.text or "").strip())
    except ValueError:
        await message.answer("Не удалось прочитать дату. Используйте формат ДД.ММ.ГГГГ.")
        return
    await state.update_data(date_to=value.isoformat())
    await state.set_state(WaitlistFlow.time_range)
    await message.answer("В какое время вам удобно?", reply_markup=waitlist_time_keyboard())


@router.callback_query(WaitlistFlow.time_range, WaitlistCallback.filter(F.action == "time"))
async def save_waitlist(
    callback: CallbackQuery,
    callback_data: WaitlistCallback,
    state: FSMContext,
    waitlist_service: WaitlistService,
) -> None:
    ranges = {
        0: (None, None),
        1: (time(9), time(13)),
        2: (time(13), time(17)),
        3: (time(17), time(21)),
    }
    data = await state.get_data()
    start, end = ranges.get(callback_data.object_id, (None, None))
    try:
        values = WaitlistCreate(
            service_id=int(data["service_id"]),
            date_from=date.fromisoformat(str(data["date_from"])),
            date_to=date.fromisoformat(str(data["date_to"])),
            preferred_time_from=start,
            preferred_time_to=end,
        )
        entry = await waitlist_service.create(actor_from_telegram(callback.from_user), values)
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Запрос #{entry.id} добавлен. Мы сообщим, когда появится подходящее окно."
        )
    await callback.answer()


@router.callback_query(WaitlistCallback.filter(F.action == "cancel"))
async def cancel_waitlist(
    callback: CallbackQuery,
    callback_data: WaitlistCallback,
    waitlist_service: WaitlistService,
) -> None:
    try:
        await waitlist_service.cancel_my(
            actor_from_telegram(callback.from_user), callback_data.entry_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer("Запрос в листе ожидания отменён.")
    await callback.answer()


@router.callback_query(WaitlistCallback.filter(F.action == "book"))
async def accept_waitlist_offer(
    callback: CallbackQuery,
    callback_data: WaitlistCallback,
    state: FSMContext,
    waitlist_service: WaitlistService,
    booking_service: BookingService,
) -> None:
    try:
        entry = await waitlist_service.get_my(
            actor_from_telegram(callback.from_user), callback_data.entry_id
        )
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user), entry.service_id
        )
        window = next(item for item in availability.windows if item.id == callback_data.object_id)
    except (DomainError, StopIteration):
        await callback.answer("Это время уже недоступно.", show_alert=True)
        return
    local_date = window.start_at.astimezone(ZoneInfo(availability.timezone)).date()
    await state.clear()
    await state.update_data(
        service_id=entry.service_id,
        window_id=window.id,
        local_date=local_date.isoformat(),
    )
    await state.set_state(BookingFlow.name)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Время выбрано. Как вас зовут?", reply_markup=booking_navigation_keyboard()
        )
    await callback.answer()
