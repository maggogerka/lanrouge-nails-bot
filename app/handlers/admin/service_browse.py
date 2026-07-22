"""Service list, details and lifecycle actions."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.errors import EntityNotFoundError, ServiceInUseError
from app.handlers.admin.service_common import actor_from_telegram, render_service
from app.keyboards.admin.main import ADMIN_SERVICES_TEXT
from app.keyboards.admin.services import (
    ServiceCallback,
    delete_confirmation_keyboard,
    service_details_keyboard,
    service_list_keyboard,
)
from app.schemas.service import AdminActor
from app.services.service_catalog import ServiceCatalog

router = Router(name="admin.service_browse")


async def show_list_message(
    message: Message,
    catalog: ServiceCatalog,
    actor: AdminActor,
) -> None:
    services = await catalog.list_services(actor)
    text = "Услуг пока нет." if not services else "Услуги: ✅ активна, ⏸ скрыта."
    await message.answer(text, reply_markup=service_list_keyboard(services))


async def show_list_callback(
    callback: CallbackQuery,
    catalog: ServiceCatalog,
    actor: AdminActor,
    *,
    answer_text: str | None = None,
) -> None:
    services = await catalog.list_services(actor)
    text = "Услуг пока нет." if not services else "Услуги: ✅ активна, ⏸ скрыта."
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=service_list_keyboard(services))
    await callback.answer(answer_text)


async def show_details_callback(
    callback: CallbackQuery,
    catalog: ServiceCatalog,
    actor: AdminActor,
    service_id: int,
) -> None:
    try:
        service = await catalog.get_service(actor, service_id)
    except EntityNotFoundError:
        await callback.answer("Услуга больше не существует.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_service(service),
            reply_markup=service_details_keyboard(service),
        )
    await callback.answer()


@router.message(F.text == ADMIN_SERVICES_TEXT)
async def show_service_list(message: Message, service_catalog: ServiceCatalog) -> None:
    if message.from_user is None:
        return
    await show_list_message(message, service_catalog, actor_from_telegram(message.from_user))


@router.callback_query(ServiceCallback.filter(F.action == "list"))
async def show_service_list_callback(
    callback: CallbackQuery,
    service_catalog: ServiceCatalog,
) -> None:
    await show_list_callback(
        callback,
        service_catalog,
        actor_from_telegram(callback.from_user),
    )


@router.callback_query(ServiceCallback.filter(F.action == "view"))
async def show_service_details(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
) -> None:
    await show_details_callback(
        callback,
        service_catalog,
        actor_from_telegram(callback.from_user),
        callback_data.service_id,
    )


@router.callback_query(ServiceCallback.filter(F.action.in_({"archive", "activate"})))
async def change_service_activity(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    try:
        service = await service_catalog.set_active(
            actor_from_telegram(callback.from_user),
            callback_data.service_id,
            is_active=callback_data.action == "activate",
            correlation_id=correlation_id,
        )
    except EntityNotFoundError:
        await callback.answer("Услуга больше не существует.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_service(service),
            reply_markup=service_details_keyboard(service),
        )
    await callback.answer("Статус услуги обновлён.")


@router.callback_query(ServiceCallback.filter(F.action == "delete_prompt"))
async def prompt_service_deletion(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Удалить услугу физически можно только если она никогда не использовалась. Продолжить?",
            reply_markup=delete_confirmation_keyboard(callback_data.service_id),
        )
    await callback.answer()


@router.callback_query(ServiceCallback.filter(F.action == "delete_confirm"))
async def confirm_service_deletion(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    try:
        await service_catalog.delete_unused_service(
            actor_from_telegram(callback.from_user),
            callback_data.service_id,
            correlation_id=correlation_id,
        )
    except ServiceInUseError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except EntityNotFoundError:
        await callback.answer("Услуга больше не существует.", show_alert=True)
        return
    await show_list_callback(
        callback,
        service_catalog,
        actor_from_telegram(callback.from_user),
        answer_text="Услуга удалена.",
    )
