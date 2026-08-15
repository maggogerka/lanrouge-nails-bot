"""Service list, details and lifecycle actions."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError, EntityNotFoundError, ServiceInUseError
from app.handlers.admin.service_common import actor_from_telegram, render_service
from app.keyboards.admin.main import ADMIN_SERVICES_TEXT
from app.keyboards.admin.services import (
    ServiceCallback,
    delete_confirmation_keyboard,
    force_delete_confirmation_keyboard,
    service_details_keyboard,
    service_list_keyboard,
)
from app.schemas.service import AdminActor
from app.services.service_catalog import ServiceCatalog
from app.utils.pagination import paginate_sequence
from app.utils.telegram import edit_text_safely

router = Router(name="admin.service_browse")
_SERVICES_PAGE_SIZE = 8


async def show_list_message(
    message: Message,
    catalog: ServiceCatalog,
    actor: AdminActor,
) -> None:
    services = await catalog.list_services(actor, include_archived=False)
    paged = paginate_sequence(services, page=1, page_size=_SERVICES_PAGE_SIZE)
    text = (
        "Активных услуг пока нет."
        if not services
        else (f"🛠 Активные услуги · страница {paged.page} из {paged.pages} · всего {paged.total}:")
    )
    await message.answer(
        text,
        reply_markup=service_list_keyboard(
            list(paged.items), include_archived=False, page=paged.page, pages=paged.pages
        ),
    )


async def show_list_callback(
    callback: CallbackQuery,
    catalog: ServiceCatalog,
    actor: AdminActor,
    *,
    include_archived: bool = False,
    page: int = 1,
    answer_text: str | None = None,
) -> None:
    services = await catalog.list_services(actor, include_archived=include_archived)
    paged = paginate_sequence(services, page=page, page_size=_SERVICES_PAGE_SIZE)
    if include_archived:
        text = "Услуг пока нет." if not services else "🛠 Все услуги: ✅ активна, ⏸ архив."
    else:
        text = "Активных услуг пока нет." if not services else "🛠 Активные услуги:"
    if services:
        text += f"\nСтраница {paged.page} из {paged.pages} · всего {paged.total}."
    changed = True
    if isinstance(callback.message, Message):
        changed = await edit_text_safely(
            callback.message,
            text,
            reply_markup=service_list_keyboard(
                list(paged.items),
                include_archived=include_archived,
                page=paged.page,
                pages=paged.pages,
            ),
        )
    await callback.answer(answer_text or (None if changed else "Список уже актуален."))


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


@router.message(F.text.in_({ADMIN_SERVICES_TEXT, "Услуги"}))
async def show_service_list(message: Message, service_catalog: ServiceCatalog) -> None:
    if message.from_user is None:
        return
    await show_list_message(message, service_catalog, actor_from_telegram(message.from_user))


@router.callback_query(ServiceCallback.filter(F.action == "list"))
async def show_service_list_callback(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
) -> None:
    await show_list_callback(
        callback,
        service_catalog,
        actor_from_telegram(callback.from_user),
        page=callback_data.page,
    )


@router.callback_query(ServiceCallback.filter(F.action == "list_archived"))
async def show_archived_service_list_callback(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
) -> None:
    await show_list_callback(
        callback,
        service_catalog,
        actor_from_telegram(callback.from_user),
        include_archived=True,
        page=callback_data.page,
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
            "Обычное удаление доступно только для неиспользованной услуги без дополнений. "
            "Принудительное удаление также удалит связанные записи, оплаты, отзывы, "
            "ожидание и дополнения.",
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


@router.callback_query(ServiceCallback.filter(F.action == "force_delete_prompt"))
async def prompt_force_service_deletion(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "⚠️ Последнее подтверждение. Услуга и вся связанная история будут удалены "
            "безвозвратно. Продолжить?",
            reply_markup=force_delete_confirmation_keyboard(callback_data.service_id),
        )
    await callback.answer()


@router.callback_query(ServiceCallback.filter(F.action == "force_delete_confirm"))
async def confirm_force_service_deletion(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    service_catalog: ServiceCatalog,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    try:
        deleted_appointments = await service_catalog.force_delete_service(
            actor,
            callback_data.service_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await show_list_callback(
        callback,
        service_catalog,
        actor,
        answer_text=f"Услуга удалена. Связанных записей удалено: {deleted_appointments}.",
    )
