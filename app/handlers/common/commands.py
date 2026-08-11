"""Transport handlers for bootstrap commands."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import StaffRole
from app.filters import IsStaff
from app.keyboards.admin.main import admin_main_keyboard
from app.keyboards.common.interface_mode import InterfaceModeCallback, interface_mode_keyboard
from app.keyboards.master.main import master_main_keyboard
from app.schemas.authorization import StaffContext
from app.services.menu_service import MenuService
from app.services.presentation_service import PresentationService

router = Router(name="common.commands")
_ALL_STAFF_ROLES = frozenset(StaffRole)


def start_text(business_name: str = "Студия") -> str:
    """Return the stage-safe greeting without unfinished menu actions."""

    return (
        f"Добро пожаловать в <b>{escape(business_name)}</b>! 💅\n\n"
        "Здесь можно будет выбрать услугу и записаться в свободное время. "
        "Онлайн-запись запускается поэтапно.\n\n"
        "Команда /whoami покажет ваш числовой Telegram ID."
    )


def whoami_text(telegram_id: int) -> str:
    """Render the sender's own numeric Telegram ID."""

    return f"Ваш Telegram ID: <code>{telegram_id}</code>"


@router.message(CommandStart(), IsStaff(allowed_roles=_ALL_STAFF_ROLES))
async def choose_staff_interface(message: Message, state: FSMContext) -> None:
    """Offer an explicit presentation mode without weakening staff permissions."""

    await state.clear()
    await message.answer("Выберите режим работы:", reply_markup=interface_mode_keyboard())


@router.callback_query(
    InterfaceModeCallback.filter(F.action == "management"),
    IsStaff(allowed_roles=_ALL_STAFF_ROLES),
)
async def open_management_interface(
    callback: CallbackQuery,
    state: FSMContext,
    staff_context: StaffContext,
    menu_service: MenuService,
    presentation_service: PresentationService,
) -> None:
    """Return to the live DB-authorized panel for the current staff role."""

    await state.clear()
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    business = await presentation_service.get_business()
    if staff_context.role is StaffRole.MASTER:
        text = (
            f"<b>{escape(business.display_name)}</b> · мастер "
            f"<b>{escape(staff_context.display_name)}</b>."
        )
        keyboard = master_main_keyboard(staff_context)
    else:
        text = f"Панель администратора <b>{escape(business.display_name)}</b>."
        keyboard = admin_main_keyboard(
            await menu_service.get_capabilities(),
            staff_context,
        )
    await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.message(Command("whoami"))
async def handle_whoami(message: Message) -> None:
    """Return only the current sender's Telegram ID."""

    if message.from_user is None:
        await message.answer("Не удалось определить Telegram ID отправителя.")
        return
    await message.answer(whoami_text(message.from_user.id))
