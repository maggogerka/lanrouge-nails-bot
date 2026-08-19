"""Implemented client information and catalog menu sections."""

from collections.abc import Sequence
from html import escape

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from app.domain.errors import DomainError
from app.handlers.client.booking_browse import show_service_cards, start_booking
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.main import (
    CLIENT_BOOK_TEXT,
    CLIENT_CONTACTS_TEXT,
    CLIENT_MASTERS_TEXT,
    CLIENT_NOTIFICATIONS_TEXT,
    CLIENT_PORTFOLIO_TEXT,
    CLIENT_PRIVACY_TEXT,
    CLIENT_REPEAT_TEXT,
    CLIENT_REVIEWS_TEXT,
    CLIENT_SERVICES_TEXT,
    CLIENT_WAITLIST_TEXT,
    client_main_keyboard,
)
from app.keyboards.client.masters import PublicMasterCallback, public_master_keyboard
from app.keyboards.client.presentation import business_links_keyboard, privacy_links_keyboard
from app.schemas.features import FeatureName
from app.schemas.presentation import PublicMasterPresentation
from app.services.booking_service import BookingService
from app.services.feature_flag_service import FeatureFlagService
from app.services.menu_service import MenuService
from app.services.presentation_service import PresentationService
from app.utils.pagination import paginate_sequence
from app.utils.telegram import (
    answer_html_safely,
    answer_photo_with_html,
    edit_photo_safely,
    edit_text_safely,
)
from app.utils.telegram_text import fits_telegram_caption, split_telegram_html

router = Router(name="client.menu")


@router.message(F.text == CLIENT_SERVICES_TEXT)
async def show_client_services(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    if message.from_user is None:
        return
    services = await booking_service.list_active_services(actor_from_telegram(message.from_user))
    if not services:
        await message.answer("Активных услуг пока нет.")
        return
    await state.clear()
    await show_service_cards(message, state, services)


@router.message(F.text == CLIENT_CONTACTS_TEXT)
async def show_contacts(
    message: Message,
    presentation_service: PresentationService,
    feature_flag_service: FeatureFlagService,
) -> None:
    try:
        await feature_flag_service.require_enabled(FeatureName.CLIENT_SUPPORT)
        info = await presentation_service.get_business()
    except DomainError:
        await message.answer("Раздел поддержки сейчас недоступен.")
        return
    details = [f"<b>{escape(info.display_name)}</b>"]
    if info.address:
        details.append(f"Адрес: {escape(info.address)}")
    if info.contact_phone:
        details.append(f"Телефон: {escape(info.contact_phone)}")
    if info.contact_email:
        details.append(f"Email: {escape(info.contact_email)}")
    if info.support_hours:
        details.append(f"Часы поддержки: {escape(info.support_hours)}")
    if info.support_instructions:
        details.append(escape(info.support_instructions))
    await message.answer(
        "\n".join(details),
        reply_markup=business_links_keyboard(info),
    )


@router.message(F.text == CLIENT_MASTERS_TEXT)
async def show_masters(
    message: Message,
    state: FSMContext,
    presentation_service: PresentationService,
) -> None:
    try:
        masters = await presentation_service.list_bookable_masters()
    except DomainError:
        await message.answer("Раздел мастеров сейчас недоступен.")
        return
    if not masters:
        await message.answer("Доступные мастера пока не опубликованы.")
        return
    await state.clear()
    await state.update_data(master_auxiliary_message_ids=[])
    await _render_master_card(message, state, masters, page=1, edit=False)


async def _remember_master_auxiliary_messages(state: FSMContext, sent: list[Message]) -> None:
    message_ids = [
        raw_message_id
        for item in sent[:-1]
        if isinstance((raw_message_id := getattr(item, "message_id", None)), int)
    ]
    await state.update_data(master_auxiliary_message_ids=message_ids)


async def _clear_master_auxiliary_messages(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    raw_message_ids = data.get("master_auxiliary_message_ids", [])
    message_ids = (
        [item for item in raw_message_ids if isinstance(item, int) and item > 0]
        if isinstance(raw_message_ids, list)
        else []
    )
    bot = message.bot
    if bot is not None:
        for message_id in message_ids:
            try:
                await bot.delete_message(message.chat.id, message_id)
            except TelegramBadRequest as exc:
                if "message to delete not found" not in str(exc).casefold():
                    raise
    await state.update_data(master_auxiliary_message_ids=[])


async def _delete_master_card_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest as exc:
        if "message to delete not found" not in str(exc).casefold():
            raise


async def _render_master_card(
    message: Message,
    state: FSMContext,
    masters: Sequence[PublicMasterPresentation],
    *,
    page: int,
    edit: bool,
) -> None:
    paged = paginate_sequence(masters, page=page, page_size=1)
    master = paged.items[0]
    lines = [
        f"<b>Мастер {paged.page} из {paged.pages}</b>",
        f"<b>{escape(master.display_name)}</b>",
    ]
    if master.specialization:
        lines.append(escape(master.specialization))
    if master.bio:
        lines.append(escape(master.bio))
    text = "\n".join(lines)
    keyboard = public_master_keyboard(
        master.staff_member_id,
        master.social_links,
        page=paged.page,
        pages=paged.pages,
    )
    photo_file_id = master.telegram_photo_file_id
    if not edit:
        if photo_file_id:
            sent = await answer_photo_with_html(message, photo_file_id, text, reply_markup=keyboard)
        else:
            sent = await answer_html_safely(message, text, reply_markup=keyboard)
        await _remember_master_auxiliary_messages(state, sent)
    elif message.photo and photo_file_id and fits_telegram_caption(text, html=True):
        await _clear_master_auxiliary_messages(message, state)
        await edit_photo_safely(
            message,
            InputMediaPhoto(media=photo_file_id, caption=text, parse_mode=ParseMode.HTML),
            reply_markup=keyboard,
        )
    elif not message.photo and not photo_file_id and len(split_telegram_html(text)) == 1:
        await _clear_master_auxiliary_messages(message, state)
        await edit_text_safely(message, text, reply_markup=keyboard)
    else:
        await _clear_master_auxiliary_messages(message, state)
        await _delete_master_card_message(message)
        if photo_file_id:
            sent = await answer_photo_with_html(message, photo_file_id, text, reply_markup=keyboard)
        else:
            sent = await answer_html_safely(message, text, reply_markup=keyboard)
        await _remember_master_auxiliary_messages(state, sent)


@router.callback_query(PublicMasterCallback.filter(F.action == "page"))
async def browse_masters(
    callback: CallbackQuery,
    callback_data: PublicMasterCallback,
    state: FSMContext,
    presentation_service: PresentationService,
) -> None:
    masters = await presentation_service.list_bookable_masters()
    if not masters:
        await callback.answer("Мастера больше не доступны.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await _render_master_card(
            callback.message, state, masters, page=callback_data.page, edit=True
        )
    await callback.answer()


@router.callback_query(PublicMasterCallback.filter(F.action == "photo"))
async def show_master_photo(
    callback: CallbackQuery,
    callback_data: PublicMasterCallback,
    presentation_service: PresentationService,
) -> None:
    del callback_data, presentation_service
    await callback.answer(
        "Фотография теперь показывается прямо в карточке мастера. Обновите раздел «Мастера».",
        show_alert=True,
    )


@router.callback_query(PublicMasterCallback.filter(F.action == "book"))
async def book_with_master(
    callback: CallbackQuery,
    callback_data: PublicMasterCallback,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    del callback_data
    if isinstance(callback.message, Message):
        await start_booking(
            callback.message,
            state,
            booking_service,
            actor=actor_from_telegram(callback.from_user),
        )
    await callback.answer()


@router.message(F.text == CLIENT_PRIVACY_TEXT)
async def show_privacy(
    message: Message,
    presentation_service: PresentationService,
) -> None:
    try:
        business = await presentation_service.get_business()
    except DomainError:
        await message.answer("Юридическая информация сейчас недоступна.")
        return
    await message.answer(
        "Здесь можно открыть юридические документы. "
        "Для запроса на удаление данных используйте /delete_my_data.",
        reply_markup=privacy_links_keyboard(business),
    )


_STALE_OPTIONAL_MENU_TEXTS = {
    CLIENT_BOOK_TEXT,
    CLIENT_NOTIFICATIONS_TEXT,
    CLIENT_PORTFOLIO_TEXT,
    CLIENT_REPEAT_TEXT,
    CLIENT_REVIEWS_TEXT,
    CLIENT_WAITLIST_TEXT,
}


@router.message(F.text.in_(_STALE_OPTIONAL_MENU_TEXTS))
async def refresh_stale_optional_menu(message: Message, menu_service: MenuService) -> None:
    """Replace a persisted Telegram reply keyboard after a feature was disabled."""

    capabilities = await menu_service.get_capabilities()
    await message.answer(
        "Этот раздел сейчас отключён. Меню уже обновлено.",
        reply_markup=client_main_keyboard(capabilities),
    )
