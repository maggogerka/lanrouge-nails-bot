"""Reusable one-tap skip control for optional text and media fields."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

OPTIONAL_SKIP_TEXT = "Пропустить"
NO_COMMENT_TEXT = "Без комментария"


def optional_input_keyboard(*, no_comment: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=NO_COMMENT_TEXT if no_comment else OPTIONAL_SKIP_TEXT)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=(
            "Комментарий необязателен" if no_comment else "Поле необязательно"
        ),
    )


def is_optional_skip(value: str) -> bool:
    return value.strip() in {"-", OPTIONAL_SKIP_TEXT, NO_COMMENT_TEXT}
