"""Administrative portfolio creation, preview and lifecycle handlers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message
from pydantic import ValidationError

from app.domain.enums import PortfolioDisplayMode, PortfolioStatus
from app.domain.errors import DomainError, EntityNotFoundError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.main import ADMIN_PORTFOLIO_TEXT
from app.keyboards.admin.portfolio import (
    PortfolioAdminCallback,
    linked_service_keyboard,
    media_collection_keyboard,
    portfolio_admin_menu,
    portfolio_details_keyboard,
    portfolio_display_keyboard,
    portfolio_list_keyboard,
    portfolio_preview_keyboard,
)
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.client.portfolio import external_portfolio_keyboard
from app.keyboards.common.optional_input import is_optional_skip, optional_input_keyboard
from app.schemas.pagination import PageRequest
from app.schemas.portfolio import (
    PortfolioCreate,
    PortfolioDisplayConfig,
    PortfolioDisplayUpdate,
    PortfolioItemView,
    PortfolioMediaInput,
)
from app.services.portfolio_service import PortfolioService
from app.services.service_catalog import ServiceCatalog
from app.states.admin_portfolio import AdminPortfolioCreate, AdminPortfolioSettings

router = Router(name="admin.portfolio")


@router.message(F.text == ADMIN_PORTFOLIO_TEXT)
async def show_portfolio_menu(message: Message) -> None:
    await message.answer("Управление портфолио:", reply_markup=portfolio_admin_menu())


@router.callback_query(PortfolioAdminCallback.filter(F.action == "menu"))
async def show_portfolio_menu_callback(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Управление портфолио:", reply_markup=portfolio_admin_menu()
        )
    await callback.answer()


@router.callback_query(PortfolioAdminCallback.filter(F.action == "display"))
async def show_portfolio_display_settings(
    callback: CallbackQuery,
    portfolio_service: PortfolioService,
) -> None:
    config = await portfolio_service.get_display_config()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_display_config(config),
            reply_markup=portfolio_display_keyboard(config),
        )
    await callback.answer()


@router.callback_query(
    PortfolioAdminCallback.filter(F.action.in_({"mode_internal", "mode_external", "mode_disabled"}))
)
async def change_portfolio_display_mode(
    callback: CallbackQuery,
    callback_data: PortfolioAdminCallback,
    portfolio_service: PortfolioService,
    correlation_id: str,
) -> None:
    modes = {
        "mode_internal": PortfolioDisplayMode.INTERNAL,
        "mode_external": PortfolioDisplayMode.EXTERNAL_LINK,
        "mode_disabled": PortfolioDisplayMode.DISABLED,
    }
    try:
        config = await portfolio_service.update_display_config(
            actor_from_telegram(callback.from_user),
            PortfolioDisplayUpdate(mode=modes[callback_data.action]),
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_display_config(config),
            reply_markup=portfolio_display_keyboard(config),
        )
    await callback.answer("Режим портфолио обновлён.")


@router.callback_query(
    PortfolioAdminCallback.filter(F.action.in_({"edit_external_url", "edit_external_text"}))
)
async def begin_portfolio_display_edit(
    callback: CallbackQuery,
    callback_data: PortfolioAdminCallback,
    state: FSMContext,
) -> None:
    if callback_data.action == "edit_external_url":
        await state.set_state(AdminPortfolioSettings.external_url)
        prompt = "Введите абсолютный HTTPS URL внешнего портфолио:"
    else:
        await state.set_state(AdminPortfolioSettings.button_text)
        prompt = "Введите текст кнопки внешнего портфолио до 100 символов:"
    if isinstance(callback.message, Message):
        await callback.message.answer(prompt, reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AdminPortfolioSettings.external_url)
async def save_external_portfolio_url(
    message: Message,
    state: FSMContext,
    portfolio_service: PortfolioService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    try:
        config = await portfolio_service.update_display_config(
            actor_from_telegram(message.from_user),
            PortfolioDisplayUpdate(external_url=(message.text or "").strip()),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        _render_display_config(config), reply_markup=portfolio_display_keyboard(config)
    )


@router.message(AdminPortfolioSettings.button_text)
async def save_external_portfolio_button_text(
    message: Message,
    state: FSMContext,
    portfolio_service: PortfolioService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    try:
        config = await portfolio_service.update_display_config(
            actor_from_telegram(message.from_user),
            PortfolioDisplayUpdate(button_text=(message.text or "").strip()),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        _render_display_config(config), reply_markup=portfolio_display_keyboard(config)
    )


@router.callback_query(PortfolioAdminCallback.filter(F.action == "display_preview"))
async def preview_portfolio_display(
    callback: CallbackQuery,
    portfolio_service: PortfolioService,
) -> None:
    config = await portfolio_service.get_display_config()
    if isinstance(callback.message, Message):
        if config.mode is PortfolioDisplayMode.EXTERNAL_LINK and config.external_url:
            await callback.message.answer(
                "Так клиент увидит внешнее портфолио:",
                reply_markup=external_portfolio_keyboard(config.external_url, config.button_text),
            )
        elif config.mode is PortfolioDisplayMode.INTERNAL:
            await callback.message.answer("Кнопка откроет встроенное портфолио Telegram.")
        else:
            await callback.message.answer("Кнопка портфолио будет скрыта.")
    await callback.answer()


@router.callback_query(
    PortfolioAdminCallback.filter(F.action.in_({"list", "published", "archived"}))
)
async def show_portfolio_list(
    callback: CallbackQuery,
    callback_data: PortfolioAdminCallback,
    portfolio_service: PortfolioService,
) -> None:
    status = {
        "published": PortfolioStatus.PUBLISHED,
        "archived": PortfolioStatus.ARCHIVED,
    }.get(callback_data.action)
    page = await portfolio_service.list_admin(
        actor_from_telegram(callback.from_user),
        PageRequest(page=callback_data.page, page_size=8),
        status=status,
    )
    text = "Работ пока нет." if not page.items else f"Работы: страница {page.page}/{page.pages}"
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=portfolio_list_keyboard(
                page.items,
                page=page.page,
                pages=page.pages,
            ),
        )
    await callback.answer()


@router.callback_query(PortfolioAdminCallback.filter(F.action == "view"))
async def show_portfolio_details(
    callback: CallbackQuery,
    callback_data: PortfolioAdminCallback,
    portfolio_service: PortfolioService,
) -> None:
    try:
        item = await portfolio_service.get_admin(
            actor_from_telegram(callback.from_user), callback_data.object_id
        )
    except EntityNotFoundError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await _send_item_preview(
            callback.message,
            item,
            reply_markup=portfolio_details_keyboard(item, page=callback_data.page),
        )
    await callback.answer()


@router.callback_query(PortfolioAdminCallback.filter(F.action.in_({"publish", "archive"})))
async def change_portfolio_status(
    callback: CallbackQuery,
    callback_data: PortfolioAdminCallback,
    portfolio_service: PortfolioService,
    correlation_id: str,
) -> None:
    status = (
        PortfolioStatus.PUBLISHED if callback_data.action == "publish" else PortfolioStatus.ARCHIVED
    )
    try:
        item = await portfolio_service.set_status(
            actor_from_telegram(callback.from_user),
            callback_data.object_id,
            status,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Статус работы обновлён.",
            reply_markup=portfolio_details_keyboard(item, page=callback_data.page),
        )
    await callback.answer("Готово")


@router.callback_query(PortfolioAdminCallback.filter(F.action == "tags"))
async def show_portfolio_tags(
    callback: CallbackQuery,
    portfolio_service: PortfolioService,
) -> None:
    tags = await portfolio_service.list_tags()
    text = (
        "Тегов пока нет. Они создаются при добавлении работы."
        if not tags
        else "Теги портфолио:\n" + "\n".join(f"• {escape(tag.name)}" for tag in tags)
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=portfolio_admin_menu())
    await callback.answer()


@router.callback_query(PortfolioAdminCallback.filter(F.action == "add"))
async def begin_portfolio_creation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminPortfolioCreate.media)
    await state.update_data(media=[])
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отправьте фотографии работы по одной. Когда закончите, нажмите «Фото загружены».",
            reply_markup=media_collection_keyboard(),
        )
    await callback.answer()


@router.message(AdminPortfolioCreate.media, F.photo)
async def collect_portfolio_photo(
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
        await message.answer(f"Уже загружено максимальное количество: {maximum}.")
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
        reply_markup=media_collection_keyboard(),
    )


@router.callback_query(
    AdminPortfolioCreate.media,
    PortfolioAdminCallback.filter(F.action == "media_done"),
)
async def finish_media_collection(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("media"):
        await callback.answer("Добавьте хотя бы одну фотографию.", show_alert=True)
        return
    await state.set_state(AdminPortfolioCreate.title)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите название работы:")
    await callback.answer()


@router.message(AdminPortfolioCreate.title)
async def capture_portfolio_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not 1 <= len(title) <= 255:
        await message.answer("Название должно содержать от 1 до 255 символов.")
        return
    await state.update_data(title=title)
    await state.set_state(AdminPortfolioCreate.description)
    await message.answer(
        "Введите короткое описание или пропустите этот шаг:",
        reply_markup=optional_input_keyboard(),
    )


@router.message(AdminPortfolioCreate.description)
async def capture_portfolio_description(
    message: Message,
    state: FSMContext,
    service_catalog: ServiceCatalog,
) -> None:
    if message.from_user is None:
        return
    raw = (message.text or "").strip()
    description = None if is_optional_skip(raw) else raw
    if description is not None and len(description) > 2000:
        await message.answer("Описание не должно превышать 2000 символов.")
        return
    await state.update_data(description=description)
    services = await service_catalog.list_services(actor_from_telegram(message.from_user))
    await state.set_state(AdminPortfolioCreate.linked_service)
    await message.answer(
        "Свяжите дизайн с услугой или выберите «Без связи»:",
        reply_markup=linked_service_keyboard(services),
    )


@router.callback_query(
    AdminPortfolioCreate.linked_service,
    PortfolioAdminCallback.filter(F.action.in_({"link", "link_none"})),
)
async def capture_linked_service(
    callback: CallbackQuery,
    callback_data: PortfolioAdminCallback,
    state: FSMContext,
) -> None:
    linked_service_id = callback_data.object_id if callback_data.action == "link" else None
    await state.update_data(linked_service_id=linked_service_id)
    await state.set_state(AdminPortfolioCreate.design_price)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите ориентировочную доплату или пропустите этот шаг:",
            reply_markup=optional_input_keyboard(),
        )
    await callback.answer()


@router.message(AdminPortfolioCreate.design_price)
async def capture_design_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = None if is_optional_skip(raw) else Decimal(raw)
    except InvalidOperation:
        price = Decimal("-1")
    exponent = price.as_tuple().exponent if price is not None else None
    if price is not None and (
        not price.is_finite() or price < 0 or not isinstance(exponent, int) or exponent < -2
    ):
        await message.answer("Введите неотрицательную сумму с максимум двумя знаками или «-».")
        return
    await state.update_data(design_price=str(price) if price is not None else None)
    await state.set_state(AdminPortfolioCreate.sort_order)
    await message.answer("Введите порядок сортировки целым числом, например 0:")


@router.message(AdminPortfolioCreate.sort_order)
async def capture_sort_order(message: Message, state: FSMContext) -> None:
    try:
        sort_order = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите целое число от -100000 до 100000.")
        return
    if not -100000 <= sort_order <= 100000:
        await message.answer("Введите целое число от -100000 до 100000.")
        return
    await state.update_data(sort_order=sort_order)
    await state.set_state(AdminPortfolioCreate.tags)
    await message.answer(
        "Введите теги через запятую или пропустите этот шаг:",
        reply_markup=optional_input_keyboard(),
    )


@router.message(AdminPortfolioCreate.tags)
async def capture_portfolio_tags(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    tags = (
        []
        if is_optional_skip(raw)
        else [value.strip() for value in raw.split(",") if value.strip()]
    )
    if len(tags) > 10 or any(len(tag) > 100 for tag in tags):
        await message.answer("Можно указать до 10 тегов, каждый не длиннее 100 символов.")
        return
    await state.update_data(tag_names=tags)
    await state.set_state(AdminPortfolioCreate.preview)
    data = await state.get_data()
    if message.photo:
        return
    media = data.get("media", [])
    if media:
        await _send_raw_preview(message, media)
    await message.answer(
        _render_creation_preview(data),
        reply_markup=portfolio_preview_keyboard(),
    )


@router.callback_query(
    AdminPortfolioCreate.preview,
    PortfolioAdminCallback.filter(F.action.in_({"save_publish", "save_draft"})),
)
async def save_portfolio_work(
    callback: CallbackQuery,
    callback_data: PortfolioAdminCallback,
    state: FSMContext,
    portfolio_service: PortfolioService,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    try:
        values = PortfolioCreate(
            title=data["title"],
            description=data.get("description"),
            linked_service_id=data.get("linked_service_id"),
            design_price=data.get("design_price"),
            sort_order=data["sort_order"],
            media=[PortfolioMediaInput.model_validate(value) for value in data["media"]],
            tag_names=data.get("tag_names", []),
        )
        item = await portfolio_service.create(
            actor_from_telegram(callback.from_user),
            values,
            publish=callback_data.action == "save_publish",
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Работа опубликована."
            if item.status is PortfolioStatus.PUBLISHED
            else "Черновик сохранён.",
            reply_markup=portfolio_details_keyboard(item, page=1),
        )
    await callback.answer("Готово")


@router.callback_query(PortfolioAdminCallback.filter(F.action == "cancel"))
async def cancel_portfolio_creation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Создание работы отменено.", reply_markup=portfolio_admin_menu()
        )
    await callback.answer()


def _render_item(item: PortfolioItemView) -> str:
    status = {
        PortfolioStatus.DRAFT: "черновик",
        PortfolioStatus.PUBLISHED: "опубликована",
        PortfolioStatus.ARCHIVED: "архив",
    }[item.status]
    tags = ", ".join(escape(tag.name) for tag in item.tags) or "—"
    design_line = f"Доплата: {item.design_price:.2f} ₽\n" if item.design_price is not None else ""
    return (
        f"<b>{escape(item.title)}</b>\n"
        f"Статус: {status}\n"
        f"Описание: {escape(item.description) if item.description else '—'}\n"
        f"Услуга: {escape(item.linked_service_name) if item.linked_service_name else '—'}\n"
        f"{design_line}"
        f"Теги: {tags}"
    )


async def _send_item_preview(
    message: Message, item: PortfolioItemView, *, reply_markup: InlineKeyboardMarkup
) -> None:
    caption = _render_item(item)
    if len(item.media) == 1:
        await message.answer_photo(
            item.media[0].telegram_file_id,
            caption=f"<b>{escape(item.title)}</b>",
        )
        await message.answer(caption, reply_markup=reply_markup)
        return
    if item.media:
        await message.answer_media_group(
            [
                InputMediaPhoto(
                    media=value.telegram_file_id,
                    caption=f"<b>{escape(item.title)}</b>" if index == 0 else None,
                )
                for index, value in enumerate(item.media)
            ]
        )
    await message.answer("Действия с работой:", reply_markup=reply_markup)


async def _send_raw_preview(message: Message, media: list[object]) -> None:
    parsed = [PortfolioMediaInput.model_validate(value) for value in media]
    if len(parsed) == 1:
        await message.answer_photo(parsed[0].telegram_file_id, caption="Предпросмотр")
    else:
        await message.answer_media_group(
            [
                InputMediaPhoto(
                    media=value.telegram_file_id,
                    caption="Предпросмотр" if index == 0 else None,
                )
                for index, value in enumerate(parsed)
            ]
        )


def _render_creation_preview(data: dict[str, object]) -> str:
    raw_tags = data.get("tag_names")
    tags = raw_tags if isinstance(raw_tags, list) else []
    return (
        "<b>Проверьте работу</b>\n\n"
        f"Название: {escape(str(data.get('title', '')))}\n"
        f"Описание: {escape(str(data.get('description') or '—'))}\n"
        f"Доплата: {escape(str(data.get('design_price') or '—'))}\n"
        f"Теги: {escape(', '.join(str(value) for value in tags) or '—')}"
    )


def _render_display_config(config: PortfolioDisplayConfig) -> str:
    labels = {
        PortfolioDisplayMode.INTERNAL: "встроенное портфолио",
        PortfolioDisplayMode.EXTERNAL_LINK: "внешняя ссылка",
        PortfolioDisplayMode.DISABLED: "отключено",
    }
    return (
        "<b>Режим портфолио</b>\n\n"
        f"Текущий режим: {labels[config.mode]}\n"
        f"Внешний URL: {escape(config.external_url) if config.external_url else '—'}\n"
        f"Текст кнопки: {escape(config.button_text)}\n\n"
        "Переключение режима не удаляет внутренние работы."
    )
