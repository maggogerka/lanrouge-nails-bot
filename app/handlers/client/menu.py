"""Implemented client menu sections and graceful v0.2 placeholders."""

from html import escape

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.main import (
    CLIENT_CONTACTS_TEXT,
    CLIENT_NOTIFICATIONS_TEXT,
    CLIENT_PREPARATION_TEXT,
    CLIENT_SERVICES_TEXT,
)
from app.services.booking_service import BookingService

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
async def show_contacts(message: Message, booking_service: BookingService) -> None:
    if message.from_user is None:
        return
    info = await booking_service.get_business_info(actor_from_telegram(message.from_user))
    await message.answer(
        f"<b>{escape(info.business_name)}</b>\nАдрес: {escape(info.address)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Открыть на карте", url=info.map_url)],
                [InlineKeyboardButton(text="Написать мастеру", url=info.master_telegram_url)],
            ]
        ),
    )


@router.message(F.text.in_({CLIENT_PREPARATION_TEXT, CLIENT_NOTIFICATIONS_TEXT}))
async def show_future_section(message: Message) -> None:
    await message.answer("Раздел появится в следующей версии.")
