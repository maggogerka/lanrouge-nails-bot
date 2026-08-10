"""Administrative menu entry point."""

from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.keyboards.admin.main import ADMIN_VENDOR_SUPPORT_TEXT, admin_main_keyboard
from app.keyboards.admin.services import CANCEL_TEXT
from app.keyboards.support import vendor_support_keyboard
from app.schemas.authorization import StaffContext
from app.services.menu_service import MenuService
from app.services.presentation_service import PresentationService
from app.services.vendor_support_service import VendorSupportService

router = Router(name="admin.menu")


@router.message(F.text.casefold() == CANCEL_TEXT.casefold())
async def cancel_admin_form(
    message: Message,
    state: FSMContext,
    menu_service: MenuService,
    presentation_service: PresentationService,
) -> None:
    """Cancel any current administrative FSM consistently."""

    if await state.get_state() is None:
        return
    await state.clear()
    business = await presentation_service.get_business()
    await message.answer(
        f"Действие отменено. Панель <b>{escape(business.display_name)}</b>.",
        reply_markup=admin_main_keyboard(await menu_service.get_capabilities()),
    )


@router.message(Command("admin"))
async def show_admin_menu(
    message: Message,
    menu_service: MenuService,
    presentation_service: PresentationService,
) -> None:
    """Display only administrative sections implemented at this stage."""

    business = await presentation_service.get_business()
    await message.answer(
        f"Панель администратора <b>{escape(business.display_name)}</b>.",
        reply_markup=admin_main_keyboard(await menu_service.get_capabilities()),
    )


@router.message(F.text == ADMIN_VENDOR_SUPPORT_TEXT)
async def show_vendor_support(
    message: Message,
    staff_context: StaffContext,
    vendor_support_service: VendorSupportService,
    correlation_id: str,
) -> None:
    text, url = vendor_support_service.render(
        staff_context,
        correlation_id=correlation_id,
    )
    await message.answer(text, reply_markup=vendor_support_keyboard(url))
