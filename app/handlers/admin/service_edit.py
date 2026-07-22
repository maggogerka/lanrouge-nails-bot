"""FSM for one-step administrative service edits."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.handlers.admin.service_common import (
    DURATION_RANGE,
    actor_from_telegram,
    parse_price,
    render_service,
)
from app.keyboards.admin.main import admin_main_keyboard
from app.keyboards.admin.services import ServiceCallback, cancel_keyboard, service_details_keyboard
from app.schemas.service import ServiceCreate, ServicePatch
from app.services.service_catalog import ServiceCatalog
from app.states.admin_service import AdminServiceEdit

router = Router(name="admin.service_edit")


async def begin_edit(
    callback: CallbackQuery,
    state: FSMContext,
    service_id: int,
    target_state: State,
    prompt: str,
) -> None:
    await state.clear()
    await state.update_data(service_id=service_id)
    await state.set_state(target_state)
    if isinstance(callback.message, Message):
        await callback.message.answer(prompt, reply_markup=cancel_keyboard())
    await callback.answer()


@router.callback_query(ServiceCallback.filter(F.action == "edit_name"))
async def begin_edit_name(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    state: FSMContext,
) -> None:
    await begin_edit(
        callback,
        state,
        callback_data.service_id,
        AdminServiceEdit.name,
        "Введите новое название:",
    )


@router.callback_query(ServiceCallback.filter(F.action == "edit_description"))
async def begin_edit_description(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    state: FSMContext,
) -> None:
    await begin_edit(
        callback,
        state,
        callback_data.service_id,
        AdminServiceEdit.description,
        "Введите новое описание или «-», чтобы очистить:",
    )


@router.callback_query(ServiceCallback.filter(F.action == "edit_price"))
async def begin_edit_price(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    state: FSMContext,
) -> None:
    await begin_edit(
        callback,
        state,
        callback_data.service_id,
        AdminServiceEdit.price,
        "Введите новую стоимость:",
    )


@router.callback_query(ServiceCallback.filter(F.action == "edit_duration"))
async def begin_edit_duration(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    state: FSMContext,
) -> None:
    await begin_edit(
        callback,
        state,
        callback_data.service_id,
        AdminServiceEdit.duration,
        "Введите диапазон минут через дефис, например 120-180:",
    )


async def finish_edit(
    message: Message,
    state: FSMContext,
    catalog: ServiceCatalog,
    patch: ServicePatch,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    service_id = int(data["service_id"])
    service = await catalog.update_service(
        actor_from_telegram(message.from_user),
        service_id,
        patch,
        correlation_id=correlation_id,
    )
    await state.clear()
    await message.answer("Изменения сохранены.", reply_markup=admin_main_keyboard())
    await message.answer(render_service(service), reply_markup=service_details_keyboard(service))


@router.message(AdminServiceEdit.name)
async def finish_edit_name(
    message: Message,
    state: FSMContext,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    try:
        patch = ServicePatch(name=message.text or "")
    except ValidationError:
        await message.answer("Название должно содержать от 1 до 255 символов.")
        return
    await finish_edit(message, state, service_catalog, patch, correlation_id)


@router.message(AdminServiceEdit.description)
async def finish_edit_description(
    message: Message,
    state: FSMContext,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    raw = (message.text or "").strip()
    try:
        patch = ServicePatch(description=None if raw == "-" else raw)
    except ValidationError:
        await message.answer("Описание не должно превышать 4000 символов.")
        return
    await finish_edit(message, state, service_catalog, patch, correlation_id)


@router.message(AdminServiceEdit.price)
async def finish_edit_price(
    message: Message,
    state: FSMContext,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    price = parse_price(message.text)
    try:
        patch = ServicePatch(price=price)
    except ValidationError:
        await message.answer("Введите неотрицательную цену, максимум с двумя знаками после точки.")
        return
    await finish_edit(message, state, service_catalog, patch, correlation_id)


@router.message(AdminServiceEdit.duration)
async def finish_edit_duration(
    message: Message,
    state: FSMContext,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    match = DURATION_RANGE.fullmatch(message.text or "")
    if match is None:
        await message.answer("Используйте формат 120-180.")
        return
    minimum, maximum = (int(value) for value in match.groups())
    try:
        patch = ServicePatch(
            duration_min_minutes=minimum,
            duration_max_minutes=maximum,
        )
        ServiceCreate(
            name="validation",
            price=0,
            duration_min_minutes=minimum,
            duration_max_minutes=maximum,
        )
    except ValidationError:
        await message.answer("Минимум не должен превышать максимум; допустимо 1–1440 минут.")
        return
    await finish_edit(message, state, service_catalog, patch, correlation_id)
