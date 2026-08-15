"""Master-owned portfolio management with service-level scope enforcement."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.enums import PortfolioStatus
from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.common.optional_input import is_optional_skip, optional_input_keyboard
from app.keyboards.master.main import MASTER_PORTFOLIO_TEXT
from app.keyboards.master.portfolio import (
    MasterPortfolioCallback,
    master_portfolio_item_keyboard,
    master_portfolio_media_keyboard,
    master_portfolio_menu,
    master_portfolio_save_keyboard,
)
from app.schemas.authorization import StaffContext
from app.schemas.pagination import PageRequest
from app.schemas.portfolio import PortfolioCreate, PortfolioItemView, PortfolioMediaInput
from app.services.portfolio_service import PortfolioService
from app.states.master_portfolio import MasterPortfolioCreate

router = Router(name="master.portfolio")


@router.message(F.text == MASTER_PORTFOLIO_TEXT)
async def show_own_portfolio(
    message: Message,
    portfolio_service: PortfolioService,
    *,
    page_number: int = 1,
) -> None:
    if message.from_user is None:
        return
    page = await portfolio_service.list_admin(
        actor_from_telegram(message.from_user),
        PageRequest(page=page_number, page_size=8),
    )
    text = (
        "<b>Моё портфолио</b>\nВы можете добавлять и изменять только свои работы."
        if page.items
        else "<b>Моё портфолио</b>\nРабот пока нет. Добавьте первую фотографию."
    )
    await message.answer(
        text,
        reply_markup=master_portfolio_menu(page.items, page=page.page, pages=page.pages),
    )


@router.callback_query(MasterPortfolioCallback.filter(F.action == "list"))
async def show_own_portfolio_callback(
    callback: CallbackQuery,
    callback_data: MasterPortfolioCallback,
    portfolio_service: PortfolioService,
) -> None:
    if isinstance(callback.message, Message):
        page = await portfolio_service.list_admin(
            actor_from_telegram(callback.from_user),
            PageRequest(page=callback_data.page, page_size=8),
        )
        text = (
            "<b>Моё портфолио</b>\nВы можете добавлять и изменять только свои работы."
            if page.items
            else "<b>Моё портфолио</b>\nРабот пока нет. Добавьте первую фотографию."
        )
        await callback.message.edit_text(
            text,
            reply_markup=master_portfolio_menu(page.items, page=page.page, pages=page.pages),
        )
    await callback.answer()


@router.callback_query(MasterPortfolioCallback.filter(F.action == "add"))
async def begin_own_portfolio_creation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(MasterPortfolioCreate.media)
    await state.set_data({"media": []})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отправьте фотографии работы по одной, затем нажмите «Фото загружены».",
            reply_markup=master_portfolio_media_keyboard(),
        )
    await callback.answer()


@router.message(MasterPortfolioCreate.media, F.photo)
async def collect_own_portfolio_photo(
    message: Message,
    state: FSMContext,
    portfolio_service: PortfolioService,
) -> None:
    if message.from_user is None or not message.photo:
        return
    data = await state.get_data()
    media = list(data.get("media", []))
    maximum = await portfolio_service.get_max_media(actor_from_telegram(message.from_user))
    if len(media) >= maximum:
        await message.answer(f"Можно загрузить не более {maximum} фотографий.")
        return
    photo = message.photo[-1]
    media.append(
        {
            "telegram_file_id": photo.file_id,
            "telegram_file_unique_id": photo.file_unique_id,
            "media_type": "photo",
        }
    )
    await state.update_data(media=media)
    await message.answer(
        f"Фото сохранено: {len(media)}/{maximum}.",
        reply_markup=master_portfolio_media_keyboard(),
    )


@router.callback_query(
    MasterPortfolioCreate.media,
    MasterPortfolioCallback.filter(F.action == "media_done"),
)
async def finish_own_media(callback: CallbackQuery, state: FSMContext) -> None:
    if not (await state.get_data()).get("media"):
        await callback.answer("Сначала добавьте хотя бы одну фотографию.", show_alert=True)
        return
    await state.set_state(MasterPortfolioCreate.title)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите название работы:")
    await callback.answer()


@router.message(MasterPortfolioCreate.title)
async def capture_own_portfolio_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not 1 <= len(title) <= 255:
        await message.answer("Название должно содержать от 1 до 255 символов.")
        return
    await state.update_data(title=title)
    await state.set_state(MasterPortfolioCreate.description)
    await message.answer(
        "Добавьте описание или пропустите этот шаг:",
        reply_markup=optional_input_keyboard(),
    )


@router.message(MasterPortfolioCreate.description)
async def capture_own_portfolio_description(
    message: Message,
    state: FSMContext,
) -> None:
    raw = (message.text or "").strip()
    description = None if is_optional_skip(raw) else raw
    if description is not None and len(description) > 2000:
        await message.answer("Описание не должно превышать 2000 символов.")
        return
    await state.update_data(description=description)
    await state.set_state(MasterPortfolioCreate.preview)
    data = await state.get_data()
    await message.answer(
        f"<b>{escape(str(data['title']))}</b>\n"
        f"{escape(description or 'Без описания')}\n\n"
        f"Фотографий: {len(data.get('media', []))}",
        reply_markup=master_portfolio_save_keyboard(),
    )


@router.callback_query(
    MasterPortfolioCreate.preview,
    MasterPortfolioCallback.filter(F.action.in_({"save_publish", "save_draft"})),
)
async def save_own_portfolio_work(
    callback: CallbackQuery,
    callback_data: MasterPortfolioCallback,
    state: FSMContext,
    staff_context: StaffContext,
    portfolio_service: PortfolioService,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    try:
        item = await portfolio_service.create(
            actor_from_telegram(callback.from_user),
            PortfolioCreate(
                title=data["title"],
                description=data.get("description"),
                media=[
                    PortfolioMediaInput.model_validate(value) for value in data.get("media", [])
                ],
                staff_member_id=staff_context.staff_member_id,
            ),
            publish=callback_data.action == "save_publish",
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render_item(item),
            reply_markup=master_portfolio_item_keyboard(item),
        )
    await callback.answer("Работа сохранена.")


@router.callback_query(MasterPortfolioCallback.filter(F.action == "view"))
async def show_own_portfolio_item(
    callback: CallbackQuery,
    callback_data: MasterPortfolioCallback,
    portfolio_service: PortfolioService,
) -> None:
    try:
        item = await portfolio_service.get_admin(
            actor_from_telegram(callback.from_user),
            callback_data.item_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render_item(item),
            reply_markup=master_portfolio_item_keyboard(item, page=callback_data.page),
        )
    await callback.answer()


@router.callback_query(MasterPortfolioCallback.filter(F.action.in_({"publish", "archive"})))
async def change_own_portfolio_status(
    callback: CallbackQuery,
    callback_data: MasterPortfolioCallback,
    portfolio_service: PortfolioService,
    correlation_id: str,
) -> None:
    status = (
        PortfolioStatus.PUBLISHED if callback_data.action == "publish" else PortfolioStatus.ARCHIVED
    )
    try:
        item = await portfolio_service.set_status(
            actor_from_telegram(callback.from_user),
            callback_data.item_id,
            status,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_item(item),
            reply_markup=master_portfolio_item_keyboard(item, page=callback_data.page),
        )
    await callback.answer("Статус обновлён.")


@router.callback_query(MasterPortfolioCallback.filter(F.action == "cancel"))
async def cancel_own_portfolio_creation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("Создание работы отменено.")
    await callback.answer()


def _render_item(item: PortfolioItemView) -> str:
    labels = {
        PortfolioStatus.DRAFT: "черновик",
        PortfolioStatus.PUBLISHED: "опубликовано",
        PortfolioStatus.ARCHIVED: "архив",
    }
    return (
        f"<b>{escape(item.title)}</b>\n"
        f"{escape(item.description or 'Без описания')}\n"
        f"Статус: {labels[item.status]}\n"
        f"Фотографий: {len(item.media)}"
    )
