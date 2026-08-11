"""Service photography and per-service addition management."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.errors import DomainError
from app.handlers.admin.service_common import (
    actor_from_telegram,
    parse_duration,
    parse_price,
)
from app.keyboards.admin.services import (
    ServiceAddonAdminCallback,
    ServiceCallback,
    addon_details_keyboard,
    addon_list_keyboard,
    addon_photo_keyboard,
    cancel_keyboard,
    service_photo_keyboard,
)
from app.schemas.service import (
    ServiceAddonCreate,
    ServiceAddonPatch,
    ServiceAddonView,
    ServicePatch,
)
from app.services.service_catalog import ServiceCatalog
from app.states.admin_service import AdminAddonCreate, AdminAddonEdit, AdminServiceEdit

router = Router(name="admin.service_media_addons")


def _render_addon(addon: ServiceAddonView) -> str:
    duration = (
        f"{addon.duration_min_minutes} мин."
        if addon.duration_min_minutes == addon.duration_max_minutes
        else f"{addon.duration_min_minutes}–{addon.duration_max_minutes} мин."
    )
    return (
        f"<b>{escape(addon.name)}</b>\n"
        f"Статус: {'активна' if addon.is_active else 'скрыта'}\n"
        f"Описание: {escape(addon.description or '—')}\n"
        f"Стоимость: {addon.price:.2f} ₽\n"
        f"Длительность: {duration}"
    )


async def _scoped_addon(
    catalog: ServiceCatalog,
    callback: CallbackQuery,
    data: ServiceAddonAdminCallback,
) -> ServiceAddonView:
    addon = await catalog.get_addon(actor_from_telegram(callback.from_user), data.addon_id)
    if addon.service_id != data.service_id:
        raise ValueError("Дополнительная услуга не относится к выбранной услуге.")
    return addon


@router.callback_query(ServiceCallback.filter(F.action == "photo_preview"))
async def preview_service_photo(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
) -> None:
    service = await service_catalog.get_service(
        actor_from_telegram(callback.from_user), callback_data.service_id
    )
    if isinstance(callback.message, Message):
        if service.telegram_photo_file_id:
            await callback.message.answer_photo(
                service.telegram_photo_file_id,
                caption=f"Фотография услуги «{escape(service.name)}»",
                reply_markup=service_photo_keyboard(service),
            )
        else:
            await callback.message.answer(
                "Фотография услуги не добавлена.",
                reply_markup=service_photo_keyboard(service),
            )
    await callback.answer()


@router.callback_query(ServiceCallback.filter(F.action == "photo_set"))
async def begin_service_photo(
    callback: CallbackQuery, callback_data: ServiceCallback, state: FSMContext
) -> None:
    await state.clear()
    await state.update_data(service_id=callback_data.service_id)
    await state.set_state(AdminServiceEdit.photo)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отправьте одну фотографию услуги.", reply_markup=cancel_keyboard()
        )
    await callback.answer()


@router.message(AdminServiceEdit.photo, F.photo)
async def save_service_photo(
    message: Message,
    state: FSMContext,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    if message.from_user is None or not message.photo:
        return
    data = await state.get_data()
    photo = message.photo[-1]
    service = await service_catalog.update_service(
        actor_from_telegram(message.from_user),
        int(data["service_id"]),
        ServicePatch(
            telegram_photo_file_id=photo.file_id,
            telegram_photo_file_unique_id=photo.file_unique_id,
        ),
        correlation_id=correlation_id,
    )
    await state.clear()
    await message.answer("Фотография сохранена.", reply_markup=service_photo_keyboard(service))


@router.message(AdminServiceEdit.photo)
async def reject_service_photo(message: Message) -> None:
    await message.answer("Отправьте фотографию или нажмите «Отмена».")


@router.callback_query(ServiceCallback.filter(F.action == "photo_delete"))
async def delete_service_photo(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    service = await service_catalog.update_service(
        actor_from_telegram(callback.from_user),
        callback_data.service_id,
        ServicePatch(
            telegram_photo_file_id=None,
            telegram_photo_file_unique_id=None,
        ),
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Фотография удалена.", reply_markup=service_photo_keyboard(service)
        )
    await callback.answer()


@router.callback_query(ServiceCallback.filter(F.action == "addons"))
async def show_addons(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
) -> None:
    addons = await service_catalog.list_addons(
        actor_from_telegram(callback.from_user), callback_data.service_id
    )
    text = "Дополнительных услуг пока нет." if not addons else "Дополнительные услуги:"
    if isinstance(callback.message, Message):
        await callback.message.answer(
            text, reply_markup=addon_list_keyboard(callback_data.service_id, addons)
        )
    await callback.answer()


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "view"))
async def view_addon(
    callback: CallbackQuery,
    callback_data: ServiceAddonAdminCallback,
    service_catalog: ServiceCatalog,
) -> None:
    try:
        addon = await _scoped_addon(service_catalog, callback, callback_data)
    except (DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render_addon(addon), reply_markup=addon_details_keyboard(addon)
        )
    await callback.answer()


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "add"))
async def begin_addon_create(
    callback: CallbackQuery,
    callback_data: ServiceAddonAdminCallback,
    state: FSMContext,
) -> None:
    await state.clear()
    await state.update_data(service_id=callback_data.service_id)
    await state.set_state(AdminAddonCreate.name)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите название дополнительной услуги:", reply_markup=cancel_keyboard()
        )
    await callback.answer()


@router.message(AdminAddonCreate.name)
async def addon_create_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 255:
        await message.answer("Название должно содержать от 1 до 255 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(AdminAddonCreate.description)
    await message.answer("Введите описание или «-», если его нет:")


@router.message(AdminAddonCreate.description)
async def addon_create_description(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if len(raw) > 4000:
        await message.answer("Описание не должно превышать 4000 символов.")
        return
    await state.update_data(description=None if raw == "-" else raw)
    await state.set_state(AdminAddonCreate.price)
    await message.answer("Введите стоимость дополнительной услуги:")


@router.message(AdminAddonCreate.price)
async def addon_create_price(message: Message, state: FSMContext) -> None:
    price = parse_price(message.text)
    if price is None or price < 0:
        await message.answer("Введите неотрицательную стоимость.")
        return
    await state.update_data(price=str(price))
    await state.set_state(AdminAddonCreate.duration)
    await message.answer("Введите точную длительность или диапазон, например 30 или 30-45:")


@router.message(AdminAddonCreate.duration)
async def addon_create_duration(message: Message, state: FSMContext) -> None:
    duration = parse_duration(message.text)
    if duration is None:
        await message.answer("Используйте формат 30 или 30-45.")
        return
    await state.update_data(duration_min=duration[0], duration_max=duration[1])
    await state.set_state(AdminAddonCreate.photo)
    await message.answer("Отправьте фотографию или «-», чтобы пропустить.")


@router.message(AdminAddonCreate.photo)
async def finish_addon_create(
    message: Message,
    state: FSMContext,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    if message.photo:
        photo = message.photo[-1]
        file_id: str | None = photo.file_id
        unique_id: str | None = photo.file_unique_id
    elif (message.text or "").strip() == "-":
        file_id = unique_id = None
    else:
        await message.answer("Отправьте фотографию или «-», чтобы пропустить.")
        return
    data = await state.get_data()
    try:
        addon = await service_catalog.create_addon(
            actor_from_telegram(message.from_user),
            ServiceAddonCreate(
                service_id=int(data["service_id"]),
                name=str(data["name"]),
                description=data.get("description"),
                price=Decimal(str(data["price"])),
                duration_min_minutes=int(data["duration_min"]),
                duration_max_minutes=int(data["duration_max"]),
                telegram_photo_file_id=file_id,
                telegram_photo_file_unique_id=unique_id,
            ),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        "Дополнительная услуга создана.\n\n" + _render_addon(addon),
        reply_markup=addon_details_keyboard(addon),
    )


async def _begin_addon_edit(
    callback: CallbackQuery,
    callback_data: ServiceAddonAdminCallback,
    state: FSMContext,
    target: object,
    prompt: str,
) -> None:
    await state.clear()
    await state.update_data(service_id=callback_data.service_id, addon_id=callback_data.addon_id)
    await state.set_state(target)  # type: ignore[arg-type]
    if isinstance(callback.message, Message):
        await callback.message.answer(prompt, reply_markup=cancel_keyboard())
    await callback.answer()


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "edit_name"))
async def begin_addon_name(
    callback: CallbackQuery, callback_data: ServiceAddonAdminCallback, state: FSMContext
) -> None:
    await _begin_addon_edit(
        callback, callback_data, state, AdminAddonEdit.name, "Введите новое название:"
    )


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "edit_description"))
async def begin_addon_description(
    callback: CallbackQuery, callback_data: ServiceAddonAdminCallback, state: FSMContext
) -> None:
    await _begin_addon_edit(
        callback,
        callback_data,
        state,
        AdminAddonEdit.description,
        "Введите описание или «-», чтобы очистить:",
    )


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "edit_price"))
async def begin_addon_price(
    callback: CallbackQuery, callback_data: ServiceAddonAdminCallback, state: FSMContext
) -> None:
    await _begin_addon_edit(
        callback, callback_data, state, AdminAddonEdit.price, "Введите новую стоимость:"
    )


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "edit_duration"))
async def begin_addon_duration(
    callback: CallbackQuery, callback_data: ServiceAddonAdminCallback, state: FSMContext
) -> None:
    await _begin_addon_edit(
        callback,
        callback_data,
        state,
        AdminAddonEdit.duration,
        "Введите точную длительность или диапазон, например 30 или 30-45:",
    )


async def _finish_addon_edit(
    message: Message,
    state: FSMContext,
    catalog: ServiceCatalog,
    patch: ServiceAddonPatch,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    addon = await catalog.update_addon(
        actor_from_telegram(message.from_user),
        int(data["addon_id"]),
        patch,
        correlation_id=correlation_id,
    )
    await state.clear()
    await message.answer(_render_addon(addon), reply_markup=addon_details_keyboard(addon))


@router.message(AdminAddonEdit.name)
async def edit_addon_name(
    message: Message, state: FSMContext, service_catalog: ServiceCatalog, correlation_id: str
) -> None:
    try:
        patch = ServiceAddonPatch(name=(message.text or "").strip())
    except ValidationError as exc:
        await message.answer(str(exc))
        return
    await _finish_addon_edit(message, state, service_catalog, patch, correlation_id)


@router.message(AdminAddonEdit.description)
async def edit_addon_description(
    message: Message, state: FSMContext, service_catalog: ServiceCatalog, correlation_id: str
) -> None:
    raw = (message.text or "").strip()
    try:
        patch = ServiceAddonPatch(description=None if raw == "-" else raw)
    except ValidationError as exc:
        await message.answer(str(exc))
        return
    await _finish_addon_edit(message, state, service_catalog, patch, correlation_id)


@router.message(AdminAddonEdit.price)
async def edit_addon_price(
    message: Message, state: FSMContext, service_catalog: ServiceCatalog, correlation_id: str
) -> None:
    try:
        patch = ServiceAddonPatch(price=parse_price(message.text))
    except ValidationError as exc:
        await message.answer(str(exc))
        return
    await _finish_addon_edit(message, state, service_catalog, patch, correlation_id)


@router.message(AdminAddonEdit.duration)
async def edit_addon_duration(
    message: Message, state: FSMContext, service_catalog: ServiceCatalog, correlation_id: str
) -> None:
    duration = parse_duration(message.text)
    if duration is None:
        await message.answer("Используйте формат 30 или 30-45.")
        return
    await _finish_addon_edit(
        message,
        state,
        service_catalog,
        ServiceAddonPatch(duration_min_minutes=duration[0], duration_max_minutes=duration[1]),
        correlation_id,
    )


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "photo_preview"))
async def preview_addon_photo(
    callback: CallbackQuery,
    callback_data: ServiceAddonAdminCallback,
    service_catalog: ServiceCatalog,
) -> None:
    try:
        addon = await _scoped_addon(service_catalog, callback, callback_data)
    except (DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        if addon.telegram_photo_file_id:
            await callback.message.answer_photo(
                addon.telegram_photo_file_id,
                caption=f"Фотография «{escape(addon.name)}»",
                reply_markup=addon_photo_keyboard(addon),
            )
        else:
            await callback.message.answer(
                "Фотография не добавлена.", reply_markup=addon_photo_keyboard(addon)
            )
    await callback.answer()


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "photo_set"))
async def begin_addon_photo(
    callback: CallbackQuery, callback_data: ServiceAddonAdminCallback, state: FSMContext
) -> None:
    await _begin_addon_edit(
        callback,
        callback_data,
        state,
        AdminAddonEdit.photo,
        "Отправьте одну фотографию:",
    )


@router.message(AdminAddonEdit.photo, F.photo)
async def save_addon_photo(
    message: Message, state: FSMContext, service_catalog: ServiceCatalog, correlation_id: str
) -> None:
    if not message.photo:
        return
    photo = message.photo[-1]
    await _finish_addon_edit(
        message,
        state,
        service_catalog,
        ServiceAddonPatch(
            telegram_photo_file_id=photo.file_id,
            telegram_photo_file_unique_id=photo.file_unique_id,
        ),
        correlation_id,
    )


@router.message(AdminAddonEdit.photo)
async def reject_addon_photo(message: Message) -> None:
    await message.answer("Отправьте фотографию или нажмите «Отмена».")


@router.callback_query(ServiceAddonAdminCallback.filter(F.action == "photo_delete"))
async def delete_addon_photo(
    callback: CallbackQuery,
    callback_data: ServiceAddonAdminCallback,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    try:
        await _scoped_addon(service_catalog, callback, callback_data)
        addon = await service_catalog.update_addon(
            actor_from_telegram(callback.from_user),
            callback_data.addon_id,
            ServiceAddonPatch(
                telegram_photo_file_id=None,
                telegram_photo_file_unique_id=None,
            ),
            correlation_id=correlation_id,
        )
    except (DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Фотография удалена.", reply_markup=addon_photo_keyboard(addon)
        )
    await callback.answer()


@router.callback_query(ServiceAddonAdminCallback.filter(F.action.in_({"archive", "activate"})))
async def change_addon_status(
    callback: CallbackQuery,
    callback_data: ServiceAddonAdminCallback,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    try:
        await _scoped_addon(service_catalog, callback, callback_data)
        addon = await service_catalog.set_addon_active(
            actor_from_telegram(callback.from_user),
            callback_data.addon_id,
            is_active=callback_data.action == "activate",
            correlation_id=correlation_id,
        )
    except (DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render_addon(addon), reply_markup=addon_details_keyboard(addon)
        )
    await callback.answer("Статус обновлён.")
