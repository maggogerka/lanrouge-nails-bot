"""Administrator client list, search, cards, tags, notes and direct actions."""

from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.crm import (
    CrmCallback,
    all_tags_keyboard,
    client_card_keyboard,
    client_list_keyboard,
    client_tags_keyboard,
    notes_keyboard,
)
from app.keyboards.admin.main import ADMIN_CLIENTS_TEXT
from app.schemas.crm import ClientCardView, ClientNoteCreate, ClientTagCreate
from app.schemas.pagination import PageRequest
from app.schemas.service import AdminActor
from app.services.booking_service import BookingService
from app.services.crm_service import CrmService
from app.states.admin_crm import AdminCrmFlow

router = Router(name="admin.crm")


@router.message(F.text == ADMIN_CLIENTS_TEXT)
async def show_clients(message: Message, crm_service: CrmService) -> None:
    if message.from_user is None:
        return
    await _send_client_list(message, crm_service, actor_from_telegram(message.from_user), page=1)


@router.callback_query(CrmCallback.filter(F.action == "list"))
async def show_clients_callback(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
) -> None:
    if isinstance(callback.message, Message):
        await _send_client_list(
            callback.message,
            crm_service,
            actor_from_telegram(callback.from_user),
            page=callback_data.page,
        )
    await callback.answer()


@router.callback_query(CrmCallback.filter(F.action == "search"))
async def begin_client_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminCrmFlow.search)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите имя, username, телефон или внутренний ID:")
    await callback.answer()


@router.message(AdminCrmFlow.search)
async def search_clients(message: Message, state: FSMContext, crm_service: CrmService) -> None:
    if message.from_user is None:
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите непустую строку поиска.")
        return
    page = await crm_service.list_clients(
        actor_from_telegram(message.from_user),
        PageRequest(page=1, page_size=10),
        query=query,
    )
    await state.clear()
    await message.answer(
        f"Найдено: {page.total}",
        reply_markup=client_list_keyboard(page.items, page=1, pages=page.pages),
    )


@router.callback_query(CrmCallback.filter(F.action == "view"))
async def show_client_card(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
) -> None:
    try:
        card = await crm_service.get_card(
            actor_from_telegram(callback.from_user), callback_data.client_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render_card(card),
            reply_markup=client_card_keyboard(card, page=callback_data.page),
        )
    await callback.answer()


@router.callback_query(CrmCallback.filter(F.action == "history"))
async def show_client_history(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
) -> None:
    card = await crm_service.get_card(
        actor_from_telegram(callback.from_user), callback_data.client_id
    )
    lines = [
        f"№{item.id}: {item.start_at:%d.%m.%Y %H:%M} — "
        f"{escape(item.service_name)} — {item.status.value}"
        for item in card.appointments
    ]
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "История пуста." if not lines else "<b>Последние записи</b>\n" + "\n".join(lines),
            reply_markup=client_card_keyboard(card),
        )
    await callback.answer()


@router.callback_query(CrmCallback.filter(F.action == "client_tags"))
async def show_client_tags(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    card = await crm_service.get_card(actor, callback_data.client_id)
    tags = await crm_service.list_tags(actor, active_only=True)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Теги клиентки:",
            reply_markup=client_tags_keyboard(card.id, tags, {tag.id for tag in card.tags}),
        )
    await callback.answer()


@router.callback_query(CrmCallback.filter(F.action.in_({"tag_assign", "tag_remove"})))
async def change_client_tag(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    try:
        if callback_data.action == "tag_assign":
            await crm_service.assign_tag(
                actor,
                client_id=callback_data.client_id,
                tag_id=callback_data.object_id,
                correlation_id=correlation_id,
            )
        else:
            await crm_service.remove_tag(
                actor,
                client_id=callback_data.client_id,
                tag_id=callback_data.object_id,
                correlation_id=correlation_id,
            )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    card = await crm_service.get_card(actor, callback_data.client_id)
    tags = await crm_service.list_tags(actor, active_only=True)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=client_tags_keyboard(card.id, tags, {tag.id for tag in card.tags})
        )
    await callback.answer("Теги обновлены")


@router.callback_query(CrmCallback.filter(F.action == "all_tags"))
async def show_all_tags(callback: CallbackQuery, crm_service: CrmService) -> None:
    tags = await crm_service.list_tags(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "CRM-теги:" if tags else "Тегов пока нет.",
            reply_markup=all_tags_keyboard(tags),
        )
    await callback.answer()


@router.callback_query(CrmCallback.filter(F.action == "tag_create"))
async def begin_tag_creation(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    state: FSMContext,
) -> None:
    await state.set_state(AdminCrmFlow.tag_create)
    await state.update_data(client_id=callback_data.client_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите тег в формате «название | emoji»; emoji необязателен:"
        )
    await callback.answer()


@router.message(AdminCrmFlow.tag_create)
async def create_client_tag(
    message: Message,
    state: FSMContext,
    crm_service: CrmService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    parts = [value.strip() for value in (message.text or "").split("|", maxsplit=1)]
    try:
        values = ClientTagCreate(
            name=parts[0] if parts else "",
            marker=parts[1] if len(parts) == 2 else None,
        )
        tag = await crm_service.create_tag(
            actor_from_telegram(message.from_user), values, correlation_id=correlation_id
        )
    except (DomainError, ValidationError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(f"Тег «{escape(tag.name)}» создан.")


@router.callback_query(CrmCallback.filter(F.action.in_({"tag_archive", "tag_activate"})))
async def change_tag_activity(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
    correlation_id: str,
) -> None:
    await crm_service.set_tag_active(
        actor_from_telegram(callback.from_user),
        callback_data.object_id,
        is_active=callback_data.action == "tag_activate",
        correlation_id=correlation_id,
    )
    tags = await crm_service.list_tags(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=all_tags_keyboard(tags))
    await callback.answer("Статус тега обновлён")


@router.callback_query(CrmCallback.filter(F.action == "notes"))
async def show_notes(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
) -> None:
    card = await crm_service.get_card(
        actor_from_telegram(callback.from_user), callback_data.client_id
    )
    text = (
        "Заметок пока нет."
        if not card.notes
        else "\n\n".join(f"<b>Заметка {note.id}</b>\n{escape(note.text)}" for note in card.notes)
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=notes_keyboard(card))
    await callback.answer()


@router.callback_query(CrmCallback.filter(F.action == "note_add"))
async def begin_note_creation(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    state: FSMContext,
) -> None:
    await state.set_state(AdminCrmFlow.note_create)
    await state.update_data(client_id=callback_data.client_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите заметку. Не указывайте медицинские, банковские и другие чувствительные данные."
        )
    await callback.answer()


@router.message(AdminCrmFlow.note_create)
async def create_note(
    message: Message,
    state: FSMContext,
    crm_service: CrmService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    try:
        note = await crm_service.add_note(
            actor_from_telegram(message.from_user),
            int(data["client_id"]),
            ClientNoteCreate(text=message.text or ""),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(f"Заметка {note.id} сохранена. Её видят только администраторы.")


@router.callback_query(CrmCallback.filter(F.action == "note_archive"))
async def archive_note(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
    correlation_id: str,
) -> None:
    await crm_service.archive_note(
        actor_from_telegram(callback.from_user),
        callback_data.object_id,
        correlation_id=correlation_id,
    )
    await callback.answer("Заметка архивирована")


@router.callback_query(CrmCallback.filter(F.action == "block"))
async def begin_self_booking_block(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    state: FSMContext,
) -> None:
    await state.set_state(AdminCrmFlow.block_reason)
    await state.update_data(client_id=callback_data.client_id)
    if isinstance(callback.message, Message):
        await callback.message.answer("Укажите короткую организационную причину или отправьте «-»:")
    await callback.answer()


@router.message(AdminCrmFlow.block_reason)
async def finish_self_booking_block(
    message: Message,
    state: FSMContext,
    crm_service: CrmService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    reason = (message.text or "").strip()
    card = await crm_service.set_self_booking_blocked(
        actor_from_telegram(message.from_user),
        int(data["client_id"]),
        blocked=True,
        reason=None if reason == "-" else reason,
        correlation_id=correlation_id,
    )
    await state.clear()
    await message.answer(
        "Самостоятельная запись запрещена.", reply_markup=client_card_keyboard(card)
    )


@router.callback_query(CrmCallback.filter(F.action == "unblock"))
async def unblock_self_booking(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    crm_service: CrmService,
    correlation_id: str,
) -> None:
    card = await crm_service.set_self_booking_blocked(
        actor_from_telegram(callback.from_user),
        callback_data.client_id,
        blocked=False,
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Самостоятельная запись снова доступна.",
            reply_markup=client_card_keyboard(card),
        )
    await callback.answer()


@router.callback_query(CrmCallback.filter(F.action == "write"))
async def begin_write_client(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    state: FSMContext,
) -> None:
    await state.set_state(AdminCrmFlow.write_client)
    await state.update_data(client_id=callback_data.client_id)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите сервисное сообщение клиентке:")
    await callback.answer()


@router.message(AdminCrmFlow.write_client)
async def write_client(
    message: Message,
    state: FSMContext,
    crm_service: CrmService,
    bot: Bot,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    card = await crm_service.get_card(
        actor_from_telegram(message.from_user), int(data["client_id"])
    )
    text = (message.text or "").strip()
    if not text or len(text) > 4000:
        await message.answer("Сообщение должно содержать от 1 до 4000 символов.")
        return
    try:
        await bot.send_message(card.telegram_id, text)
    except TelegramAPIError:
        await message.answer("Не удалось отправить сообщение: клиентка недоступна в Telegram.")
        return
    await state.clear()
    await message.answer("Сообщение отправлено.")


@router.callback_query(CrmCallback.filter(F.action == "manual"))
async def begin_manual_booking(
    callback: CallbackQuery,
    callback_data: CrmCallback,
    state: FSMContext,
) -> None:
    await state.set_state(AdminCrmFlow.manual_booking)
    await state.update_data(client_id=callback_data.client_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите внутренние ID услуги и открытого окна через пробел, например: 3 17. "
            "Они доступны в административных разделах услуг и окон."
        )
    await callback.answer()


@router.message(AdminCrmFlow.manual_booking)
async def create_manual_booking(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    parts = (message.text or "").split()
    data = await state.get_data()
    try:
        service_id, window_id = (int(value) for value in parts)
        receipt = await booking_service.book_for_client(
            actor_from_telegram(message.from_user),
            client_id=int(data["client_id"]),
            service_id=service_id,
            window_id=window_id,
            correlation_id=correlation_id,
        )
    except (DomainError, KeyError, TypeError, ValueError) as exc:
        await message.answer(str(exc) or "Введите ровно два числовых ID через пробел.")
        return
    await state.clear()
    await message.answer(f"Запись №{receipt.appointment_id} создана.")


async def _send_client_list(
    message: Message,
    service: CrmService,
    actor: AdminActor,
    *,
    page: int,
) -> None:
    result = await service.list_clients(actor, PageRequest(page=page, page_size=10))
    await message.answer(
        "Клиенток пока нет." if not result.items else f"Клиентки: {result.total}",
        reply_markup=client_list_keyboard(result.items, page=result.page, pages=result.pages),
    )


def _render_card(card: ClientCardView) -> str:
    subscription = "включена" if card.marketing_subscribed else "выключена"
    booking = "запрещена" if card.is_self_booking_blocked else "разрешена"
    tags = ", ".join(escape(tag.name) for tag in card.tags) or "—"
    return (
        f"<b>{escape(card.display_name)}</b>\n"
        f"Внутренний ID: {card.id}\n"
        f"Telegram: @{escape(card.username) if card.username else '—'}\n"
        f"Телефон: {escape(card.phone) if card.phone else '—'}\n"
        f"Выполнено: {card.completed_visits}; отмен: {card.cancellations}; "
        f"неявок: {card.no_shows}\n"
        f"Рекламная подписка: {subscription}\n"
        f"Самостоятельная запись: {booking}\n"
        f"Теги: {tags}"
    )
