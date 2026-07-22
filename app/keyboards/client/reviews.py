"""Client review form and public-review controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ReviewCallback(CallbackData, prefix="rev"):
    action: str
    appointment_id: int = 0
    value: int = 0


def review_request_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оставить отзыв",
                    callback_data=ReviewCallback(
                        action="start", appointment_id=appointment_id
                    ).pack(),
                )
            ]
        ]
    )


def review_rating_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{rating} ⭐",
                    callback_data=ReviewCallback(
                        action="rating", appointment_id=appointment_id, value=rating
                    ).pack(),
                )
                for rating in range(1, 6)
            ]
        ]
    )


def review_skip_text_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без текста",
                    callback_data=ReviewCallback(
                        action="skip_text", appointment_id=appointment_id
                    ).pack(),
                )
            ]
        ]
    )


def review_publication_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Разрешаю публикацию",
                    callback_data=ReviewCallback(
                        action="consent", appointment_id=appointment_id, value=1
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Только для мастера",
                    callback_data=ReviewCallback(
                        action="consent", appointment_id=appointment_id, value=0
                    ).pack(),
                )
            ],
        ]
    )


def review_confirmation_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить отзыв",
                    callback_data=ReviewCallback(
                        action="confirm", appointment_id=appointment_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=ReviewCallback(
                        action="cancel", appointment_id=appointment_id
                    ).pack(),
                )
            ],
        ]
    )
