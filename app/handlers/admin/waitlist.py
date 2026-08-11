"""Administrator waitlist overview, messaging, offers and archival."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import WaitlistStatus
from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.main import ADMIN_WAITLIST_TEXT
from app.keyboards.admin.waitlist import (
    AdminWaitlistCallback,
    admin_waitlist_entry_keyboard,
    admin_waitlist_keyboard,
)
from app.schemas.pagination import PageRequest
from app.services.waitlist_service import WaitlistService
from app.states.waitlist import AdminWaitlistFlow

router = Router(name="admin.waitlist")


async def _show(
    message: Message,
    service: WaitlistService,
    *,
    status: WaitlistStatus | None,
) -> None:
    if message.from_user is None:
        return
    page = await service.list_admin(
        actor_from_telegram(message.from_user),
        status=status,
        page=PageRequest(page_size=20),
    )
    await message.answer(
        f"Лист ожидания: {page.total} запросов.",
        reply_markup=admin_waitlist_keyboard(page.items),
    )


@router.message(F.text == ADMIN_WAITLIST_TEXT)
async def show_admin_waitlist(message: Message, waitlist_service: WaitlistService) -> None:
    await _show(message, waitlist_service, status=WaitlistStatus.ACTIVE)


@router.callback_query(AdminWaitlistCallback.filter(F.action.in_({"active", "all"})))
async def filter_admin_waitlist(
    callback: CallbackQuery,
    callback_data: AdminWaitlistCallback,
    waitlist_service: WaitlistService,
) -> None:
    if isinstance(callback.message, Message):
        await _show(
            callback.message,
            waitlist_service,
            status=WaitlistStatus.ACTIVE if callback_data.action == "active" else None,
        )
    await callback.answer()


@router.callback_query(AdminWaitlistCallback.filter(F.action == "view"))
async def view_admin_waitlist(
    callback: CallbackQuery,
    callback_data: AdminWaitlistCallback,
    waitlist_service: WaitlistService,
) -> None:
    page = await waitlist_service.list_admin(
        actor_from_telegram(callback.from_user), page=PageRequest(page_size=50)
    )
    entry = next((item for item in page.items if item.id == callback_data.entry_id), None)
    if entry is None:
        await callback.answer("Запрос не найден.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Запрос #{entry.id}\nКлиент: {entry.client_name}\n"
            f"Telegram ID: {entry.client_telegram_id}\nУслуга: {entry.service_name}\n"
            f"Даты: {entry.date_from:%d.%m.%Y}–{entry.date_to:%d.%m.%Y}\n"
            f"Статус: {entry.status.value}",
            reply_markup=admin_waitlist_entry_keyboard(entry.id),
        )
    await callback.answer()


@router.callback_query(AdminWaitlistCallback.filter(F.action == "write"))
async def begin_waitlist_message(
    callback: CallbackQuery, callback_data: AdminWaitlistCallback, state: FSMContext
) -> None:
    await state.set_state(AdminWaitlistFlow.message)
    await state.update_data(waitlist_entry_id=callback_data.entry_id)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите сообщение клиенту:")
    await callback.answer()


@router.message(AdminWaitlistFlow.message)
async def send_waitlist_message(
    message: Message,
    state: FSMContext,
    waitlist_service: WaitlistService,
    bot: Bot,
) -> None:
    if message.from_user is None or not (message.text or "").strip():
        return
    data = await state.get_data()
    page = await waitlist_service.list_admin(
        actor_from_telegram(message.from_user), page=PageRequest(page_size=50)
    )
    entry = next((item for item in page.items if item.id == int(data["waitlist_entry_id"])), None)
    if entry is None:
        await message.answer("Запрос не найден.")
        await state.clear()
        return
    try:
        await bot.send_message(entry.client_telegram_id, (message.text or "").strip())
    except TelegramAPIError:
        await message.answer("Не удалось доставить сообщение.")
        return
    await state.clear()
    await message.answer("Сообщение отправлено.")


@router.callback_query(AdminWaitlistCallback.filter(F.action == "offer"))
async def begin_waitlist_offer(
    callback: CallbackQuery, callback_data: AdminWaitlistCallback, state: FSMContext
) -> None:
    await state.set_state(AdminWaitlistFlow.offer_window)
    await state.update_data(waitlist_entry_id=callback_data.entry_id)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите ID открытого окна:")
    await callback.answer()


@router.message(AdminWaitlistFlow.offer_window)
async def offer_waitlist_window(
    message: Message, state: FSMContext, waitlist_service: WaitlistService
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    try:
        queued = await waitlist_service.offer_window(
            actor_from_telegram(message.from_user),
            int(data["waitlist_entry_id"]),
            int((message.text or "").strip()),
        )
    except (DomainError, KeyError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        "Уведомление поставлено в очередь."
        if queued
        else "Для этого окна уведомление уже создавалось."
    )


@router.callback_query(AdminWaitlistCallback.filter(F.action == "archive"))
async def archive_waitlist(
    callback: CallbackQuery,
    callback_data: AdminWaitlistCallback,
    waitlist_service: WaitlistService,
) -> None:
    try:
        await waitlist_service.archive_admin(
            actor_from_telegram(callback.from_user), callback_data.entry_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer("Запрос архивирован.")
    await callback.answer()
