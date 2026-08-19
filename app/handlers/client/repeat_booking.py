"""Compatibility response for stale repeat-booking callback buttons."""

from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.client.main import client_main_keyboard
from app.keyboards.client.repeat_booking import RepeatBookingCallback
from app.services.menu_service import MenuService

router = Router(name="client.repeat_booking")


@router.callback_query(RepeatBookingCallback.filter())
async def stale_repeat_booking_callback(
    callback: CallbackQuery,
    state: FSMContext,
    menu_service: MenuService,
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Раздел «Повторить запись» больше не используется. Для новой записи нажмите "
            "«✨ Записаться».",
            reply_markup=client_main_keyboard(await menu_service.get_capabilities()),
        )
    await callback.answer("Меню обновлено")
