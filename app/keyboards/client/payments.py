"""Client controls for the manual prepayment hand-off."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ManualPaymentCallback(CallbackData, prefix="mpay"):
    action: str
    payment_id: int


def manual_payment_report_button(payment_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="✅ Я оплатил",
        callback_data=ManualPaymentCallback(action="paid", payment_id=payment_id).pack(),
    )


def receipt_choice_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📎 Прикрепить чек",
                    callback_data=ManualPaymentCallback(
                        action="attach", payment_id=payment_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=ManualPaymentCallback(
                        action="skip", payment_id=payment_id
                    ).pack(),
                )
            ],
        ]
    )
