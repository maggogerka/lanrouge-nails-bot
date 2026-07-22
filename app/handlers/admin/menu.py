"""Administrative menu entry point."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.keyboards.admin.main import admin_main_keyboard

router = Router(name="admin.menu")


@router.message(Command("admin"))
async def show_admin_menu(message: Message) -> None:
    """Display only administrative sections implemented at this stage."""

    await message.answer(
        "Панель администратора lanrouge nails.",
        reply_markup=admin_main_keyboard(),
    )
