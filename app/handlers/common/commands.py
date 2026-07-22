"""Transport handlers for bootstrap commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="common.commands")


def start_text() -> str:
    """Return the stage-safe greeting without unfinished menu actions."""

    return (
        "Добро пожаловать в <b>lanrouge nails</b>! 💅\n\n"
        "Здесь можно будет выбрать услугу и записаться в свободное время. "
        "Онлайн-запись запускается поэтапно.\n\n"
        "Команда /whoami покажет ваш числовой Telegram ID."
    )


def whoami_text(telegram_id: int) -> str:
    """Render the sender's own numeric Telegram ID."""

    return f"Ваш Telegram ID: <code>{telegram_id}</code>"


@router.message(Command("whoami"))
async def handle_whoami(message: Message) -> None:
    """Return only the current sender's Telegram ID."""

    if message.from_user is None:
        await message.answer("Не удалось определить Telegram ID отправителя.")
        return
    await message.answer(whoami_text(message.from_user.id))
