"""Administrator waitlist overview, messaging, offers and archival."""

from __future__ import annotations

from html import escape

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
from app.schemas.waitlist import AdminWaitlistView
from app.services.waitlist_service import WaitlistService
from app.states.waitlist import AdminWaitlistFlow
from app.utils.telegram import edit_text_safely

router = Router(name="admin.waitlist")


def _render_entry(entry: AdminWaitlistView) -> str:
    return (
        f"Запрос #{entry.id}\nКлиент: {escape(entry.client_name)}\n"
        f"Telegram ID: {entry.client_telegram_id}\n"
        f"Услуга: {escape(entry.service_name)}\n"
        f"Даты: {entry.date_from:%d.%m.%Y}–{entry.date_to:%d.%m.%Y}\n"
        f"Статус: {entry.status.value}"
    )


async def _show(
    target: Message | CallbackQuery,
    service: WaitlistService,
    *,
    status: WaitlistStatus | None,
    page_number: int = 1,
) -> None:
    if target.from_user is None:
        return
    page = await service.list_admin(
        actor_from_telegram(target.from_user),
        status=status,
        page=PageRequest(page=page_number, page_size=8),
    )
    text = f"Лист ожидания: {page.total} запросов · страница {page.page} из {page.pages}."
    keyboard = admin_waitlist_keyboard(
        page.items,
        page=page.page,
        pages=page.pages,
        list_action="active" if status is WaitlistStatus.ACTIVE else "all",
    )
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            await edit_text_safely(target.message, text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


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
            callback,
            waitlist_service,
            status=WaitlistStatus.ACTIVE if callback_data.action == "active" else None,
            page_number=callback_data.page,
        )
    await callback.answer()


@router.callback_query(AdminWaitlistCallback.filter(F.action == "view"))
async def view_admin_waitlist(
    callback: CallbackQuery,
    callback_data: AdminWaitlistCallback,
    waitlist_service: WaitlistService,
) -> None:
    try:
        entry = await waitlist_service.get_admin(
            actor_from_telegram(callback.from_user), callback_data.entry_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render_entry(entry),
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
    try:
        entry = await waitlist_service.get_admin(
            actor_from_telegram(message.from_user), int(data["waitlist_entry_id"])
        )
    except (DomainError, KeyError, ValueError) as exc:
        await message.answer(str(exc), parse_mode=None)
        await state.clear()
        return
    try:
        await bot.send_message(
            entry.client_telegram_id,
            (message.text or "").strip(),
            parse_mode=None,
        )
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
        await message.answer(str(exc), parse_mode=None)
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
