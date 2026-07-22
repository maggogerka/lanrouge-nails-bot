"""Administrative menu entry point."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.keyboards.admin.main import admin_main_keyboard
from app.keyboards.admin.services import CANCEL_TEXT
from app.services.menu_service import MenuService

router = Router(name="admin.menu")


@router.message(F.text.casefold() == CANCEL_TEXT.casefold())
async def cancel_admin_form(message: Message, state: FSMContext, menu_service: MenuService) -> None:
    """Cancel any current administrative FSM consistently."""

    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer(
        "Действие отменено.",
        reply_markup=admin_main_keyboard(await menu_service.get_capabilities()),
    )


@router.message(Command("admin"))
async def show_admin_menu(message: Message, menu_service: MenuService) -> None:
    """Display only administrative sections implemented at this stage."""

    await message.answer(
        "Панель администратора lanrouge nails.",
        reply_markup=admin_main_keyboard(await menu_service.get_capabilities()),
    )
