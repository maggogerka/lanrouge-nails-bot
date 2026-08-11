"""FSM for administrative service creation."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from pydantic import ValidationError

from app.handlers.admin.service_common import (
    actor_from_telegram,
    parse_positive_minutes,
    parse_price,
    render_service,
)
from app.keyboards.admin.services import (
    ServiceCallback,
    cancel_keyboard,
    service_details_keyboard,
)
from app.keyboards.common.optional_input import is_optional_skip, optional_input_keyboard
from app.schemas.service import ServiceCreate
from app.services.service_catalog import ServiceCatalog
from app.states.admin_service import AdminServiceCreate

router = Router(name="admin.service_create")


@router.callback_query(ServiceCallback.filter(F.action == "add"))
async def begin_service_creation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminServiceCreate.name)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите название услуги:", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AdminServiceCreate.name)
async def capture_service_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 255:
        await message.answer("Название должно содержать от 1 до 255 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(AdminServiceCreate.description)
    await message.answer(
        "Введите описание или пропустите этот шаг:", reply_markup=optional_input_keyboard()
    )


@router.message(AdminServiceCreate.description)
async def capture_service_description(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    description = None if is_optional_skip(raw) else raw
    if description is not None and len(description) > 4000:
        await message.answer("Описание не должно превышать 4000 символов.")
        return
    await state.update_data(description=description)
    await state.set_state(AdminServiceCreate.price)
    await message.answer("Введите стоимость в рублях, например 2500 или 2500.50:")


@router.message(AdminServiceCreate.price)
async def capture_service_price(message: Message, state: FSMContext) -> None:
    price = parse_price(message.text)
    exponent = price.as_tuple().exponent if price is not None else None
    if (
        price is None
        or not price.is_finite()
        or price < 0
        or not isinstance(exponent, int)
        or exponent < -2
    ):
        await message.answer("Введите неотрицательную цену, максимум с двумя знаками после точки.")
        return
    await state.update_data(price=str(price))
    await state.set_state(AdminServiceCreate.duration_min)
    await message.answer("Введите минимальную продолжительность в минутах:")


@router.message(AdminServiceCreate.duration_min)
async def capture_service_duration_min(message: Message, state: FSMContext) -> None:
    minutes = parse_positive_minutes(message.text)
    if minutes is None:
        await message.answer("Введите целое число минут от 1 до 1440.")
        return
    await state.update_data(duration_min_minutes=minutes)
    await state.set_state(AdminServiceCreate.duration_max)
    await message.answer("Введите максимальную продолжительность в минутах:")


@router.message(AdminServiceCreate.duration_max)
async def capture_service_duration_max(
    message: Message,
    state: FSMContext,
) -> None:
    maximum = parse_positive_minutes(message.text)
    if maximum is None:
        await message.answer("Введите целое число минут от 1 до 1440.")
        return
    data = await state.get_data()
    minimum = data.get("duration_min_minutes")
    if not isinstance(minimum, int) or minimum > maximum:
        await message.answer("Максимальная длительность не может быть меньше минимальной.")
        return
    await state.update_data(duration_max_minutes=maximum)
    await state.set_state(AdminServiceCreate.prepayment)
    await message.answer(
        "Введите фиксированную предоплату в рублях. Отправьте 0, чтобы отключить предоплату:"
    )


@router.message(AdminServiceCreate.prepayment)
async def finish_service_creation(
    message: Message,
    state: FSMContext,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    prepayment = parse_price(message.text)
    if prepayment is None:
        await message.answer("Введите сумму, например 500, или 0 для отключения.")
        return
    data = await state.get_data()
    try:
        values = ServiceCreate(
            name=data["name"],
            description=data.get("description"),
            price=data["price"],
            duration_min_minutes=data["duration_min_minutes"],
            duration_max_minutes=data["duration_max_minutes"],
            prepayment_amount=prepayment,
        )
    except (ValidationError, KeyError):
        await message.answer(
            "Предоплата должна быть положительной либо равна нулю и не превышать цену услуги."
        )
        return
    service = await service_catalog.create_service(
        actor_from_telegram(message.from_user),
        values,
        correlation_id=correlation_id,
    )
    await state.clear()
    await message.answer(
        "Услуга создана.\n\n" + render_service(service),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Управление услугой:", reply_markup=service_details_keyboard(service))
