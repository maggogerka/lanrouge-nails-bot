"""Implemented client information and catalog menu sections."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError
from app.handlers.client.booking_browse import show_service_cards
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
from app.services.booking_service import BookingService
from app.services.feature_flag_service import FeatureFlagService
from app.services.menu_service import MenuService
from app.services.presentation_service import PresentationService

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
    for master in masters:
        lines = [f"<b>{escape(master.display_name)}</b>"]
        if master.specialization:
            lines.append(escape(master.specialization))
        if master.bio:
            lines.append(escape(master.bio))
        text = "\n".join(lines)
        keyboard = public_master_keyboard(master.staff_member_id)
        if master.telegram_photo_file_id:
            await message.answer_photo(
                master.telegram_photo_file_id,
                caption=text[:1024],
                reply_markup=keyboard,
            )
        else:
            await message.answer(text[:4096], reply_markup=keyboard)


@router.callback_query(PublicMasterCallback.filter(F.action == "book"))
async def book_with_master(
    callback: CallbackQuery,
    callback_data: PublicMasterCallback,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    try:
        services = await booking_service.list_active_services_for_master(
            actor_from_telegram(callback.from_user),
            callback_data.staff_member_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not services:
        await callback.answer("У мастера пока нет доступных услуг.", show_alert=True)
        return
    await state.clear()
    if isinstance(callback.message, Message):
        await show_service_cards(
            callback.message,
            state,
            services,
            preferred_staff_member_id=callback_data.staff_member_id,
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
