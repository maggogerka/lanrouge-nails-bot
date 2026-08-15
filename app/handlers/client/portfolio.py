"""Published portfolio browsing, tag filters, sharing and design booking hand-off."""

from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message
from aiogram.utils.deep_linking import create_start_link

from app.domain.enums import PortfolioDisplayMode
from app.domain.errors import DomainError
from app.handlers.client.booking_browse import show_service_cards, start_booking
from app.handlers.client.booking_common import available_dates
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.booking import dates_keyboard
from app.keyboards.client.main import CLIENT_PORTFOLIO_TEXT, client_main_keyboard
from app.keyboards.client.portfolio import (
    PortfolioClientCallback,
    external_portfolio_keyboard,
    portfolio_card_keyboard,
    portfolio_masters_keyboard,
    portfolio_tags_keyboard,
)
from app.schemas.booking import ClientActor
from app.schemas.pagination import PageRequest
from app.schemas.portfolio import PortfolioItemView
from app.services.booking_service import BookingService
from app.services.menu_service import MenuService
from app.services.portfolio_service import PortfolioService
from app.states.booking import BookingFlow

router = Router(name="client.portfolio")


@router.message(F.text == CLIENT_PORTFOLIO_TEXT)
async def show_portfolio(
    message: Message,
    portfolio_service: PortfolioService,
    bot: Bot,
) -> None:
    if message.from_user is None:
        return
    config = await portfolio_service.get_display_config()
    if config.mode is PortfolioDisplayMode.DISABLED:
        await message.answer("Портфолио сейчас отключено.")
        return
    if config.mode is PortfolioDisplayMode.EXTERNAL_LINK:
        if config.external_url is None:
            await message.answer("Внешнее портфолио временно недоступно.")
            return
        await message.answer(
            "Портфолио мастера:",
            reply_markup=external_portfolio_keyboard(
                config.external_url,
                config.button_text,
            ),
        )
        return
    masters = await portfolio_service.list_published_masters()
    if not masters:
        await message.answer("Мастера пока не добавили опубликованные работы.")
        return
    await message.answer(
        "Чьё портфолио хотите посмотреть?",
        reply_markup=portfolio_masters_keyboard(masters),
    )


@router.callback_query(PortfolioClientCallback.filter(F.action == "master"))
async def select_portfolio_master(
    callback: CallbackQuery,
    callback_data: PortfolioClientCallback,
    portfolio_service: PortfolioService,
    bot: Bot,
) -> None:
    if isinstance(callback.message, Message):
        await _show_page_message(
            callback.message,
            portfolio_service,
            actor_from_telegram(callback.from_user),
            bot,
            page_number=1,
            tag_id=0,
            staff_member_id=callback_data.staff_member_id,
        )
    await callback.answer()


@router.callback_query(PortfolioClientCallback.filter(F.action == "page"))
async def change_portfolio_page(
    callback: CallbackQuery,
    callback_data: PortfolioClientCallback,
    portfolio_service: PortfolioService,
    bot: Bot,
) -> None:
    if isinstance(callback.message, Message):
        await _show_page_message(
            callback.message,
            portfolio_service,
            actor_from_telegram(callback.from_user),
            bot,
            page_number=callback_data.page,
            tag_id=callback_data.tag_id,
            staff_member_id=callback_data.staff_member_id,
        )
    await callback.answer()


@router.callback_query(PortfolioClientCallback.filter(F.action == "tags"))
async def show_tag_filters(
    callback: CallbackQuery,
    callback_data: PortfolioClientCallback,
    portfolio_service: PortfolioService,
) -> None:
    tags = await portfolio_service.list_tags()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Выберите тег:" if tags else "Активных тегов пока нет.",
            reply_markup=portfolio_tags_keyboard(
                tags,
                staff_member_id=callback_data.staff_member_id,
            ),
        )
    await callback.answer()


@router.callback_query(PortfolioClientCallback.filter(F.action.in_({"similar", "book"})))
async def start_booking_from_portfolio(
    callback: CallbackQuery,
    callback_data: PortfolioClientCallback,
    state: FSMContext,
    portfolio_service: PortfolioService,
    booking_service: BookingService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    if callback_data.action == "book":
        await start_booking(
            callback.message,
            state,
            booking_service,
            actor=actor_from_telegram(callback.from_user),
        )
        await callback.answer()
        return
    try:
        item = await portfolio_service.get_published(
            actor_from_telegram(callback.from_user), callback_data.object_id
        )
        services = await booking_service.list_active_services(
            actor_from_telegram(callback.from_user)
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await state.update_data(design_reference_id=item.id, design_title=item.title)
    if item.linked_service_id is None:
        await show_service_cards(callback.message, state, services)
        await callback.answer()
        return
    linked = next((value for value in services if value.id == item.linked_service_id), None)
    if linked is None:
        await callback.message.answer(
            "Связанная услуга сейчас недоступна. Выберите другую действующую услугу:"
        )
        await show_service_cards(callback.message, state, services)
        await callback.answer()
        return
    availability = await booking_service.list_availability(
        actor_from_telegram(callback.from_user), item.linked_service_id
    )
    dates = available_dates(availability.windows)
    await state.update_data(service_id=item.linked_service_id)
    await state.set_state(BookingFlow.date)
    if not dates:
        await callback.message.answer(
            "Для выбранного дизайна сейчас нет свободных окон. Вы сможете добавить запрос "
            "в лист ожидания."
        )
    else:
        await callback.message.answer(
            f"Вы выбрали дизайн «{escape(item.title)}». Выберите дату:",
            reply_markup=dates_keyboard(dates),
        )
    await callback.answer()


@router.callback_query(PortfolioClientCallback.filter(F.action == "close"))
async def close_portfolio(callback: CallbackQuery, menu_service: MenuService) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Главное меню:",
            reply_markup=client_main_keyboard(await menu_service.get_capabilities()),
        )
    await callback.answer()


async def show_deep_linked_portfolio_item(
    message: Message,
    portfolio_service: PortfolioService,
    bot: Bot,
    item_id: int,
) -> None:
    """Render a shared work for an already-onboarded Telegram user."""

    if message.from_user is None:
        return
    try:
        item = await portfolio_service.get_published(
            actor_from_telegram(message.from_user), item_id
        )
    except DomainError as exc:
        await message.answer(str(exc))
        return
    await _send_item(message, item, bot, page=1, pages=1, tag_id=0)


async def _show_page_message(
    message: Message,
    portfolio_service: PortfolioService,
    actor: ClientActor,
    bot: Bot,
    *,
    page_number: int,
    tag_id: int,
    staff_member_id: int,
) -> None:
    try:
        page = await portfolio_service.list_published(
            actor,
            PageRequest(page=page_number, page_size=1),
            tag_id=tag_id or None,
            staff_member_id=staff_member_id,
        )
    except (DomainError, ValueError) as exc:
        await message.answer(str(exc))
        return
    if not page.items:
        await message.answer("Мастер пока не добавила работы. Загляните немного позже ")
        return
    await _send_item(
        message,
        page.items[0],
        bot,
        page=page.page,
        pages=page.pages,
        tag_id=tag_id,
    )


async def _send_item(
    message: Message,
    item: PortfolioItemView,
    bot: Bot,
    *,
    page: int,
    pages: int,
    tag_id: int,
) -> None:
    caption = _render_item(item)
    share_url = await create_start_link(bot, f"portfolio_{item.id}", encode=False)
    keyboard = portfolio_card_keyboard(
        item,
        page=page,
        pages=pages,
        tag_id=tag_id,
        share_url=share_url,
    )
    if len(item.media) == 1:
        await message.answer_photo(
            item.media[0].telegram_file_id,
            caption=f"<b>{escape(item.title)}</b>",
        )
        await message.answer(caption, reply_markup=keyboard)
        return
    await message.answer_media_group(
        [
            InputMediaPhoto(
                media=value.telegram_file_id,
                caption=f"<b>{escape(item.title)}</b>" if index == 0 else None,
            )
            for index, value in enumerate(item.media)
        ]
    )
    await message.answer(caption, reply_markup=keyboard)


def _render_item(item: PortfolioItemView) -> str:
    master_line = f"Мастер: {escape(item.master_name)}\n" if item.master_name else ""
    service_line = (
        f"Связанная услуга: {escape(item.linked_service_name)}\n"
        if item.linked_service_name
        else ""
    )
    price_line = (
        f"Ориентировочная доплата: {item.design_price:.2f} ₽\n"
        if item.design_price is not None
        else ""
    )
    tags_line = " ".join(f"#{escape(tag.name)}" for tag in item.tags)
    return (
        f"<b>{escape(item.title)}</b>\n"
        f"{master_line}"
        f"{escape(item.description) if item.description else ''}\n"
        f"{service_line}{price_line}{tags_line}"
    ).strip()
