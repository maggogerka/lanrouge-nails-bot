"""Implemented client information and catalog menu sections."""

from html import escape

from aiogram import F, Router
from aiogram.types import Message

from app.domain.errors import DomainError
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.main import (
    CLIENT_CONTACTS_TEXT,
    CLIENT_MASTERS_TEXT,
    CLIENT_PRIVACY_TEXT,
    CLIENT_SERVICES_TEXT,
)
from app.keyboards.client.presentation import business_links_keyboard, privacy_links_keyboard
from app.schemas.features import FeatureName
from app.services.booking_service import BookingService
from app.services.feature_flag_service import FeatureFlagService
from app.services.presentation_service import PresentationService

router = Router(name="client.menu")


@router.message(F.text == CLIENT_SERVICES_TEXT)
async def show_client_services(message: Message, booking_service: BookingService) -> None:
    if message.from_user is None:
        return
    services = await booking_service.list_active_services(actor_from_telegram(message.from_user))
    if not services:
        await message.answer("Активных услуг пока нет.")
        return
    cards = [
        (
            f"<b>{escape(service.name)}</b>\n"
            f"{escape(service.description) if service.description else ''}\n"
            f"Стоимость: {service.price:.2f} ₽\n"
            "Продолжительность: примерно "
            f"{service.duration_min_minutes}–{service.duration_max_minutes} мин."
        ).strip()
        for service in services
    ]
    await message.answer("\n\n".join(cards))


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
    feature_flag_service: FeatureFlagService,
) -> None:
    try:
        await feature_flag_service.require_enabled(FeatureName.MASTER_SELECTION)
        masters = await presentation_service.list_bookable_masters()
    except DomainError:
        await message.answer("Раздел мастеров сейчас недоступен.")
        return
    if not masters:
        await message.answer("Доступные мастера пока не опубликованы.")
        return
    cards = []
    for master in masters:
        lines = [f"<b>{escape(master.display_name)}</b>"]
        if master.specialization:
            lines.append(escape(master.specialization))
        if master.bio:
            lines.append(escape(master.bio))
        cards.append("\n".join(lines))
    await message.answer("\n\n".join(cards))


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
