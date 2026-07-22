"""Telegram broadcast builder, safe preview, explicit launch and results."""

from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import User as TelegramUser
from pydantic import ValidationError

from app.domain.enums import (
    BroadcastAudienceType,
    BroadcastButtonType,
    BroadcastRecipientStatus,
    BroadcastStatus,
)
from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.broadcasts import (
    BroadcastCallback,
    audience_keyboard,
    broadcast_list_keyboard,
    broadcasts_menu_keyboard,
    button_type_keyboard,
    media_done_keyboard,
    preview_keyboard,
    result_keyboard,
)
from app.keyboards.admin.main import ADMIN_BROADCASTS_TEXT
from app.schemas.broadcast import (
    BroadcastCreate,
    BroadcastDelivery,
    BroadcastMediaInput,
    BroadcastResult,
)
from app.schemas.pagination import PageRequest
from app.services.broadcast_service import BroadcastService
from app.states.broadcast import BroadcastFlow
from app.workers.broadcasts import send_delivery

router = Router(name="admin.broadcasts")


@router.message(F.text == ADMIN_BROADCASTS_TEXT)
async def show_broadcasts_menu(message: Message) -> None:
    await message.answer("Рассылки lanrouge nails", reply_markup=broadcasts_menu_keyboard())


@router.callback_query(BroadcastCallback.filter(F.action == "menu"))
async def show_broadcasts_menu_callback(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Рассылки lanrouge nails", reply_markup=broadcasts_menu_keyboard()
        )
    await callback.answer()


@router.callback_query(BroadcastCallback.filter(F.action == "add"))
async def begin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BroadcastFlow.title)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите внутреннее название рассылки:")
    await callback.answer()


@router.message(BroadcastFlow.title)
async def broadcast_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title or len(title) > 255:
        await message.answer("Название должно содержать от 1 до 255 символов.")
        return
    await state.update_data(title=title)
    await state.set_state(BroadcastFlow.text)
    await message.answer("Введите текст рассылки. Он будет отправлен как безопасный обычный текст:")


@router.message(BroadcastFlow.text)
async def broadcast_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or len(text) > 4096:
        await message.answer("Текст должен содержать от 1 до 4096 символов.")
        return
    await state.update_data(text=text, media=[])
    await state.set_state(BroadcastFlow.media)
    await message.answer(
        "Отправьте до нескольких фотографий по одной, затем нажмите «Готово».",
        reply_markup=media_done_keyboard(),
    )


@router.message(BroadcastFlow.media, F.photo)
async def collect_broadcast_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        return
    data = await state.get_data()
    media = list(data.get("media", []))
    if len(media) >= 10:
        await message.answer("Достигнут максимальный предел фотографий.")
        return
    photo = message.photo[-1]
    media.append(
        {
            "telegram_file_id": photo.file_id,
            "telegram_file_unique_id": photo.file_unique_id,
        }
    )
    await state.update_data(media=media)
    await message.answer(f"Добавлено фото: {len(media)}.", reply_markup=media_done_keyboard())


@router.callback_query(BroadcastFlow.media, BroadcastCallback.filter(F.action == "media_done"))
async def finish_media(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer("Выберите аудиторию:", reply_markup=audience_keyboard())
    await callback.answer()


@router.callback_query(BroadcastCallback.filter(F.action == "audience"))
async def choose_audience(
    callback: CallbackQuery, callback_data: BroadcastCallback, state: FSMContext
) -> None:
    presets: dict[str, tuple[BroadcastAudienceType, dict[str, object]]] = {
        "all": (BroadcastAudienceType.ALL_SUBSCRIBED, {}),
        "completed": (
            BroadcastAudienceType.ALL_SUBSCRIBED,
            {"completed_only": True},
        ),
        "without_future": (
            BroadcastAudienceType.ALL_SUBSCRIBED,
            {"without_future_booking": True},
        ),
        "inactive": (BroadcastAudienceType.INACTIVE_DAYS, {"days": 30}),
    }
    if callback_data.value in {"tag", "service"}:
        await state.update_data(pending_audience=callback_data.value)
        await state.set_state(BroadcastFlow.audience_parameter)
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Введите внутренний ID тега:"
                if callback_data.value == "tag"
                else "Введите внутренний ID услуги:"
            )
        await callback.answer()
        return
    audience_type, parameters = presets[callback_data.value]
    await state.update_data(audience_type=audience_type.value, audience_parameters=parameters)
    if isinstance(callback.message, Message):
        await callback.message.answer("Добавить кнопку?", reply_markup=button_type_keyboard())
    await callback.answer()


@router.message(BroadcastFlow.audience_parameter)
async def audience_parameter(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        object_id = int((message.text or "").strip())
        if object_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите положительный числовой ID.")
        return
    pending = str(data["pending_audience"])
    audience_type = (
        BroadcastAudienceType.CLIENT_TAG
        if pending == "tag"
        else BroadcastAudienceType.SERVICE_HISTORY
    )
    parameter = "tag_id" if pending == "tag" else "service_id"
    await state.update_data(
        audience_type=audience_type.value,
        audience_parameters={parameter: object_id},
    )
    await message.answer("Добавить кнопку?", reply_markup=button_type_keyboard())


@router.callback_query(BroadcastCallback.filter(F.action == "button"))
async def choose_button(
    callback: CallbackQuery,
    callback_data: BroadcastCallback,
    state: FSMContext,
    broadcast_service: BroadcastService,
    correlation_id: str,
) -> None:
    if callback_data.value == "url":
        await state.update_data(button_type="url")
        await state.set_state(BroadcastFlow.button_url)
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Введите кнопку в формате: Текст кнопки | https://example.com"
            )
        await callback.answer()
        return
    labels = {
        "none": None,
        "book": "Записаться",
        "portfolio": "Посмотреть работы",
        "available_windows": "Свободные окна",
    }
    await state.update_data(
        button_type=callback_data.value, button_text=labels[callback_data.value]
    )
    if isinstance(callback.message, Message):
        await _save_draft(
            callback.message,
            callback.from_user,
            state,
            broadcast_service,
            correlation_id,
        )
    await callback.answer()


@router.message(BroadcastFlow.button_url)
async def enter_button_url(
    message: Message,
    state: FSMContext,
    broadcast_service: BroadcastService,
    correlation_id: str,
) -> None:
    parts = [part.strip() for part in (message.text or "").split("|", maxsplit=1)]
    if len(parts) != 2:
        await message.answer("Используйте формат: Текст кнопки | https://example.com")
        return
    await state.update_data(button_text=parts[0], button_url=parts[1])
    if message.from_user is not None:
        await _save_draft(message, message.from_user, state, broadcast_service, correlation_id)


async def _save_draft(
    target: Message | None,
    telegram_user: TelegramUser,
    state: FSMContext,
    service: BroadcastService,
    correlation_id: str,
) -> None:
    if not isinstance(target, Message):
        return
    data = await state.get_data()
    try:
        values = BroadcastCreate(
            title=str(data["title"]),
            text=str(data["text"]),
            audience_type=BroadcastAudienceType(str(data["audience_type"])),
            audience_parameters=dict(data.get("audience_parameters", {})),
            button_type=BroadcastButtonType(str(data.get("button_type", "none"))),
            button_text=data.get("button_text"),
            button_url=data.get("button_url"),
            media=[BroadcastMediaInput.model_validate(item) for item in data.get("media", [])],
        )
        draft = await service.create_draft(
            actor_from_telegram(telegram_user), values, correlation_id=correlation_id
        )
        estimated = await service.estimate_audience(actor_from_telegram(telegram_user), draft.id)
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await target.answer(str(exc))
        return
    await state.clear()
    await target.answer(
        f"<b>Предпросмотр рассылки #{draft.id}</b>\n"
        f"Название: {escape(draft.title)}\n"
        f"Получателей примерно: {estimated}\n"
        f"Фотографий: {len(draft.media)}\n"
        f"Кнопка: {escape(draft.button_text or 'нет')}\n\n"
        f"{escape(draft.text)}\n\n"
        "После запуска изменить получателей будет нельзя.",
        reply_markup=preview_keyboard(draft.id),
    )


@router.callback_query(BroadcastCallback.filter(F.action == "test"))
async def test_broadcast(
    callback: CallbackQuery,
    callback_data: BroadcastCallback,
    broadcast_service: BroadcastService,
    bot: Bot,
) -> None:
    result = await broadcast_service.get_result(
        actor_from_telegram(callback.from_user), callback_data.broadcast_id
    )
    view = result.broadcast
    await send_delivery(
        bot,
        BroadcastDelivery(
            recipient_id=0,
            broadcast_id=view.id,
            recipient_user_id=0,
            recipient_telegram_id=callback.from_user.id,
            attempts=0,
            text=view.text,
            button_type=view.button_type,
            button_text=view.button_text,
            button_url=view.button_url,
            media=view.media,
        ),
    )
    await callback.answer("Тест отправлен только вам.")


@router.callback_query(BroadcastCallback.filter(F.action == "launch"))
async def launch_broadcast(
    callback: CallbackQuery,
    callback_data: BroadcastCallback,
    broadcast_service: BroadcastService,
    correlation_id: str,
) -> None:
    try:
        result = await broadcast_service.launch(
            actor_from_telegram(callback.from_user),
            callback_data.broadcast_id,
            confirmed=True,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Рассылка запущена. Snapshot аудитории: {result.total} получателей."
        )
    await callback.answer()


@router.callback_query(BroadcastCallback.filter(F.action == "schedule"))
async def schedule_broadcast(
    callback: CallbackQuery, callback_data: BroadcastCallback, state: FSMContext
) -> None:
    await state.update_data(broadcast_id=callback_data.broadcast_id)
    await state.set_state(BroadcastFlow.schedule)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите дату и время: ДД.ММ.ГГГГ ЧЧ:ММ")
    await callback.answer()


@router.message(BroadcastFlow.schedule)
async def save_broadcast_schedule(
    message: Message,
    state: FSMContext,
    broadcast_service: BroadcastService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    try:
        local = datetime.strptime((message.text or "").strip(), "%d.%m.%Y %H:%M").replace(
            tzinfo=ZoneInfo("Europe/Moscow")
        )
        broadcast_id = int((await state.get_data())["broadcast_id"])
        result = await broadcast_service.launch(
            actor_from_telegram(message.from_user),
            broadcast_id,
            confirmed=True,
            scheduled_at=local,
            correlation_id=correlation_id,
        )
    except (DomainError, KeyError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(f"Рассылка запланирована для {result.total} получателей.")


@router.callback_query(BroadcastCallback.filter(F.action == "edit"))
async def edit_broadcast(
    callback: CallbackQuery,
    callback_data: BroadcastCallback,
    state: FSMContext,
    broadcast_service: BroadcastService,
) -> None:
    await broadcast_service.cancel(
        actor_from_telegram(callback.from_user), callback_data.broadcast_id
    )
    await state.clear()
    await state.set_state(BroadcastFlow.title)
    if isinstance(callback.message, Message):
        await callback.message.answer("Черновик отменён. Введите новое название:")
    await callback.answer()


@router.callback_query(BroadcastCallback.filter(F.action == "cancel"))
async def cancel_broadcast(
    callback: CallbackQuery,
    callback_data: BroadcastCallback,
    broadcast_service: BroadcastService,
    correlation_id: str,
) -> None:
    try:
        await broadcast_service.cancel(
            actor_from_telegram(callback.from_user),
            callback_data.broadcast_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer("Рассылка отменена; новые сообщения не отправятся.")
    await callback.answer()


@router.callback_query(BroadcastCallback.filter(F.action == "list"))
async def list_broadcasts(
    callback: CallbackQuery,
    callback_data: BroadcastCallback,
    broadcast_service: BroadcastService,
) -> None:
    status = BroadcastStatus(callback_data.value) if callback_data.value else None
    page = await broadcast_service.list_broadcasts(
        actor_from_telegram(callback.from_user),
        status=status,
        page=PageRequest(page_size=20),
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Найдено рассылок: {page.total}",
            reply_markup=broadcast_list_keyboard(page.items),
        )
    await callback.answer()


@router.callback_query(BroadcastCallback.filter(F.action == "result"))
async def broadcast_result(
    callback: CallbackQuery,
    callback_data: BroadcastCallback,
    broadcast_service: BroadcastService,
) -> None:
    result = await broadcast_service.get_result(
        actor_from_telegram(callback.from_user), callback_data.broadcast_id
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render_result(result), reply_markup=result_keyboard(result.broadcast)
        )
    await callback.answer()


def _render_result(result: BroadcastResult) -> str:
    counts = result.counts
    return (
        f"Рассылка #{result.broadcast.id}: {escape(result.broadcast.title)}\n"
        f"Статус: {result.broadcast.status.value}\nВсего: {result.total}\n"
        f"Отправлено: {counts.get(BroadcastRecipientStatus.SENT, 0)}\n"
        f"Пропущено: {counts.get(BroadcastRecipientStatus.SKIPPED, 0)}\n"
        f"Заблокировано: {counts.get(BroadcastRecipientStatus.BLOCKED, 0)}\n"
        f"Отписались: {counts.get(BroadcastRecipientStatus.UNSUBSCRIBED, 0)}\n"
        f"Ошибки: {counts.get(BroadcastRecipientStatus.FAILED, 0)}"
    )
