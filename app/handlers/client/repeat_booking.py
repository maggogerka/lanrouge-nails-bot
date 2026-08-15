"""Fast repeat booking from the latest completed appointment."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.domain.errors import DomainError
from app.handlers.client.booking_common import available_dates
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.booking import BookingCallback, dates_keyboard
from app.keyboards.client.main import CLIENT_REPEAT_TEXT
from app.keyboards.client.repeat_booking import RepeatBookingCallback
from app.keyboards.client.waitlist import WaitlistCallback
from app.services.booking_service import BookingService
from app.services.repeat_booking_service import RepeatBookingService
from app.states.booking import BookingFlow
from app.utils.pricing import format_rub_price

router = Router(name="client.repeat_booking")


async def _show_repeat_offer(
    target: Message | CallbackQuery,
    state: FSMContext,
    repeat_service: RepeatBookingService,
    booking_service: BookingService,
) -> None:
    if target.from_user is None:
        return
    actor = actor_from_telegram(target.from_user)
    try:
        offer = await repeat_service.get_offer(actor)
    except DomainError as exc:
        message = target.message if isinstance(target, CallbackQuery) else target
        if isinstance(message, Message):
            await message.answer(str(exc))
        return
    message = target.message if isinstance(target, CallbackQuery) else target
    if not isinstance(message, Message):
        return
    if not offer.service_active:
        await message.answer(
            "Услуга из прошлой записи сейчас недоступна. Выберите действующую услугу "
            "или уточните варианты у мастера.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Выбрать другую услугу",
                            callback_data=BookingCallback(
                                action="back_services", object_id=0
                            ).pack(),
                        )
                    ],
                    *(
                        [
                            [
                                InlineKeyboardButton(
                                    text="Написать мастеру", url=offer.master_telegram_url
                                )
                            ]
                        ]
                        if offer.master_telegram_url
                        else []
                    ),
                ]
            ),
        )
        return
    availability = await booking_service.list_availability(actor, offer.service_id)
    assert offer.current_price is not None
    price_text = f"Текущая цена: {format_rub_price(offer.current_price)}."
    if offer.price_changed:
        price_text += f" Ранее было {format_rub_price(offer.previous_price)}."
    dates = available_dates(availability.windows)
    if not dates:
        await message.answer(
            f"{offer.service_name}. {price_text}\nСейчас свободных окон нет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Добавить в лист ожидания",
                            callback_data=WaitlistCallback(
                                action="service", object_id=offer.service_id
                            ).pack(),
                        )
                    ]
                ]
            ),
        )
        return
    await state.clear()
    await state.update_data(service_id=offer.service_id)
    await state.set_state(BookingFlow.date)
    await message.answer(
        f"Повторяем услугу «{offer.service_name}». {price_text}\nВыберите новую дату:",
        reply_markup=dates_keyboard(dates),
    )


@router.message(F.text == CLIENT_REPEAT_TEXT)
async def repeat_from_menu(
    message: Message,
    state: FSMContext,
    repeat_booking_service: RepeatBookingService,
    booking_service: BookingService,
) -> None:
    await _show_repeat_offer(message, state, repeat_booking_service, booking_service)


@router.callback_query(RepeatBookingCallback.filter(F.action == "start"))
async def repeat_from_reminder(
    callback: CallbackQuery,
    state: FSMContext,
    repeat_booking_service: RepeatBookingService,
    booking_service: BookingService,
) -> None:
    await _show_repeat_offer(callback, state, repeat_booking_service, booking_service)
    await callback.answer()


@router.callback_query(RepeatBookingCallback.filter(F.action == "opt_out"))
async def opt_out_repeat_reminders(
    callback: CallbackQuery,
    repeat_booking_service: RepeatBookingService,
    correlation_id: str,
) -> None:
    await repeat_booking_service.opt_out(
        actor_from_telegram(callback.from_user), correlation_id=correlation_id
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Напоминания о повторной записи отключены. Сервисные сообщения о ваших "
            "действующих записях продолжат приходить."
        )
    await callback.answer()
