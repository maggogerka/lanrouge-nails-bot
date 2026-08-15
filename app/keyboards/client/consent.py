"""Inline controls for separate privacy and marketing decisions."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ConsentCallback(CallbackData, prefix="consent"):
    """Compact onboarding callback payload."""

    action: str


def privacy_consent_keyboard(policy_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Политика конфиденциальности", url=policy_url)],
            [
                InlineKeyboardButton(
                    text="✅ Подтверждаю согласие",
                    callback_data=ConsentCallback(action="privacy_accept").pack(),
                )
            ],
        ]
    )


def marketing_consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, хочу получать новости",
                    callback_data=ConsentCallback(action="marketing_accept").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет, только сервисные сообщения",
                    callback_data=ConsentCallback(action="marketing_decline").pack(),
                )
            ],
        ]
    )


def deletion_request_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отправить запрос",
                    callback_data=ConsentCallback(action="deletion_confirm").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=ConsentCallback(action="deletion_cancel").pack(),
                )
            ],
        ]
    )
