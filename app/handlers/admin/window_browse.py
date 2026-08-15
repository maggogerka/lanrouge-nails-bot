"""Browse and mutate existing availability windows."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError, EntityNotFoundError
from app.handlers.admin.service_common import actor_from_telegram
from app.handlers.admin.window_common import render_window
from app.keyboards.admin.main import ADMIN_WINDOWS_TEXT
from app.keyboards.admin.windows import (
    WindowCallback,
    delete_window_confirmation_keyboard,
    force_delete_window_confirmation_keyboard,
    window_details_keyboard,
    window_list_keyboard,
)
from app.schemas.authorization import StaffContext
from app.schemas.service import AdminActor
from app.security.destructive_confirmation import (
    DestructiveConfirmationService,
    DestructiveObjectType,
)
from app.services.availability_service import AvailabilityService
from app.utils.pagination import paginate_sequence
from app.utils.telegram import edit_text_safely

router = Router(name="admin.window_browse")
_WINDOWS_PAGE_SIZE = 8


async def show_windows_message(
    message: Message,
    service: AvailabilityService,
    actor: AdminActor,
) -> None:
    schedule = await service.list_windows(actor, include_archived=False)
    paged = paginate_sequence(schedule.windows, page=1, page_size=_WINDOWS_PAGE_SIZE)
    text = "Активных будущих окон пока нет." if not schedule.windows else "Активные окна:"
    await message.answer(
        text,
        reply_markup=window_list_keyboard(
            list(paged.items), include_archived=False, page=paged.page, pages=paged.pages
        ),
    )


async def show_windows_callback(
    callback: CallbackQuery,
    service: AvailabilityService,
    actor: AdminActor,
    *,
    include_archived: bool = False,
    page: int = 1,
    answer_text: str | None = None,
) -> None:
    schedule = await service.list_windows(actor, include_archived=include_archived)
    paged = paginate_sequence(schedule.windows, page=page, page_size=_WINDOWS_PAGE_SIZE)
    if include_archived:
        text = "Будущих окон пока нет." if not schedule.windows else "Все будущие окна:"
    else:
        text = "Активных будущих окон пока нет." if not schedule.windows else "Активные окна:"
    if schedule.windows:
        text += f"\nСтраница {paged.page} из {paged.pages} · всего {paged.total}."
    changed = True
    if isinstance(callback.message, Message):
        changed = await edit_text_safely(
            callback.message,
            text,
            reply_markup=window_list_keyboard(
                list(paged.items),
                include_archived=include_archived,
                page=paged.page,
                pages=paged.pages,
            ),
        )
    await callback.answer(answer_text or (None if changed else "Список уже актуален."))


async def show_window_details_callback(
    callback: CallbackQuery,
    service: AvailabilityService,
    actor: AdminActor,
    window_id: int,
) -> None:
    try:
        window = await service.get_window(actor, window_id)
    except EntityNotFoundError:
        await callback.answer("Окно больше не существует.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_window(window),
            reply_markup=window_details_keyboard(window),
        )
    await callback.answer()


@router.message(F.text == ADMIN_WINDOWS_TEXT)
async def show_windows(message: Message, availability_service: AvailabilityService) -> None:
    if message.from_user is None:
        return
    await show_windows_message(
        message,
        availability_service,
        actor_from_telegram(message.from_user),
    )


@router.callback_query(WindowCallback.filter(F.action == "list"))
async def show_windows_from_callback(
    callback: CallbackQuery,
    callback_data: WindowCallback,
    availability_service: AvailabilityService,
) -> None:
    await show_windows_callback(
        callback,
        availability_service,
        actor_from_telegram(callback.from_user),
        page=callback_data.page,
    )


@router.callback_query(WindowCallback.filter(F.action == "list_archived"))
async def show_archived_windows_from_callback(
    callback: CallbackQuery,
    callback_data: WindowCallback,
    availability_service: AvailabilityService,
) -> None:
    await show_windows_callback(
        callback,
        availability_service,
        actor_from_telegram(callback.from_user),
        include_archived=True,
        page=callback_data.page,
    )


@router.callback_query(WindowCallback.filter(F.action == "view"))
async def show_window_details(
    callback: CallbackQuery,
    callback_data: WindowCallback,
    availability_service: AvailabilityService,
) -> None:
    await show_window_details_callback(
        callback,
        availability_service,
        actor_from_telegram(callback.from_user),
        callback_data.window_id,
    )


@router.callback_query(WindowCallback.filter(F.action.in_({"close", "reopen"})))
async def change_window_status(
    callback: CallbackQuery,
    callback_data: WindowCallback,
    availability_service: AvailabilityService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    try:
        if callback_data.action == "close":
            window = await availability_service.close_window(
                actor,
                callback_data.window_id,
                correlation_id=correlation_id,
            )
        else:
            window = await availability_service.reopen_window(
                actor,
                callback_data.window_id,
                correlation_id=correlation_id,
            )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_window(window),
            reply_markup=window_details_keyboard(window),
        )
    await callback.answer("Статус окна обновлён.")


@router.callback_query(WindowCallback.filter(F.action == "delete_prompt"))
async def prompt_window_deletion(
    callback: CallbackQuery,
    callback_data: WindowCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Обычное удаление доступно только для окна без записей. "
            "Принудительное удаление безвозвратно удалит окно, связанные записи, "
            "оплаты, отзывы и уведомления.",
            reply_markup=delete_window_confirmation_keyboard(callback_data.window_id),
        )
    await callback.answer()


@router.callback_query(WindowCallback.filter(F.action == "delete_confirm"))
async def confirm_window_deletion(
    callback: CallbackQuery,
    callback_data: WindowCallback,
    availability_service: AvailabilityService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    try:
        await availability_service.delete_unused_window(
            actor,
            callback_data.window_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await show_windows_callback(
        callback,
        availability_service,
        actor,
        answer_text="Окно удалено.",
    )


@router.callback_query(WindowCallback.filter(F.action == "force_delete_prompt"))
async def prompt_force_window_deletion(
    callback: CallbackQuery,
    callback_data: WindowCallback,
    destructive_confirmation_service: DestructiveConfirmationService,
    staff_context: StaffContext,
) -> None:
    try:
        await destructive_confirmation_service.issue(
            staff_context,
            DestructiveObjectType.WINDOW,
            callback_data.window_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "⚠️ Последнее подтверждение. Окно и вся связанная с ним история будут "
            "удалены безвозвратно. Продолжить?",
            reply_markup=force_delete_window_confirmation_keyboard(callback_data.window_id),
        )
    await callback.answer()


@router.callback_query(WindowCallback.filter(F.action == "force_delete_confirm"))
async def confirm_force_window_deletion(
    callback: CallbackQuery,
    callback_data: WindowCallback,
    availability_service: AvailabilityService,
    destructive_confirmation_service: DestructiveConfirmationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    try:
        await destructive_confirmation_service.consume(
            staff_context,
            DestructiveObjectType.WINDOW,
            callback_data.window_id,
        )
        deleted_appointments = await availability_service.force_delete_window(
            actor,
            callback_data.window_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await show_windows_callback(
        callback,
        availability_service,
        actor,
        answer_text=f"Окно удалено. Связанных записей удалено: {deleted_appointments}.",
    )
