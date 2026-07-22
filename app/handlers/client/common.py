"""Shared client identity conversion and safe formatting."""

from aiogram.types import User as TelegramUser

from app.schemas.booking import ClientActor


def actor_from_telegram(user: TelegramUser) -> ClientActor:
    """Copy the minimum Telegram identity into an application DTO."""

    return ClientActor(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )
