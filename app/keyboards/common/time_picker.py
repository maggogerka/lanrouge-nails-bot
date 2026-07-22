"""Reusable 24-hour inline time picker."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class TimePickerCallback(CallbackData, prefix="atp"):
    action: str
    value: str


def time_picker_keyboard(step_minutes: int = 60) -> InlineKeyboardMarkup:
    values = clock_values(step_minutes)
    buttons = [
        InlineKeyboardButton(
            text=value,
            callback_data=TimePickerCallback(
                action="pick",
                value=value.replace(":", ""),
            ).pack(),
        )
        for value in values
    ]
    rows = [buttons[index : index + 4] for index in range(0, len(buttons), 4)]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="✏️ Ввести другое время",
                    callback_data=TimePickerCallback(action="manual", value="-").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Выбрать другую дату",
                    callback_data=TimePickerCallback(action="date", value="-").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=TimePickerCallback(action="cancel", value="-").pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Назад к выбору времени",
                    callback_data=TimePickerCallback(action="back", value="-").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=TimePickerCallback(action="cancel", value="-").pack(),
                )
            ],
        ]
    )


def clock_values(step_minutes: int = 60) -> tuple[str, ...]:
    if step_minutes <= 0 or step_minutes > 24 * 60 or (24 * 60) % step_minutes:
        raise ValueError("time step must be a positive divisor of 1440")
    return tuple(f"{total // 60:02d}:{total % 60:02d}" for total in range(0, 24 * 60, step_minutes))


def decode_clock_value(value: str) -> str | None:
    """Decode compact HHMM callback payload into display/parser format."""

    if len(value) != 4 or not value.isdecimal():
        return None
    return f"{value[:2]}:{value[2:]}"
