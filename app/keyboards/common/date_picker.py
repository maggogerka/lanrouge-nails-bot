"""Reusable Telegram inline calendar for administrative date selection."""

from __future__ import annotations

from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.date_picker_service import DatePickerPage


class DatePickerCallback(CallbackData, prefix="adp"):
    """Compact action plus ISO date; every value is revalidated server-side."""

    action: str
    value: str


def date_picker_keyboard(page: DatePickerPage) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    date_buttons: list[InlineKeyboardButton] = []
    for day in page.days:
        action = "pick" if day.selectable else "off"
        label = day.label if day.selectable else f"🚫 {day.label}"
        date_buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=DatePickerCallback(
                    action=action,
                    value=day.local_date.isoformat(),
                ).pack(),
            )
        )
    rows.extend(date_buttons[index : index + 3] for index in range(0, len(date_buttons), 3))

    rows.append(
        [
            _navigation_button("◀️ Предыдущие даты", page.previous_start),
            InlineKeyboardButton(
                text="📍 Сегодня",
                callback_data=DatePickerCallback(
                    action="pick",
                    value=page.today.isoformat(),
                ).pack(),
            ),
            _navigation_button("Следующие даты ▶️", page.next_start),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data=DatePickerCallback(action="back", value="-").pack(),
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=DatePickerCallback(action="cancel", value="-").pack(),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _navigation_button(text: str, start: date | None) -> InlineKeyboardButton:
    if start is None:
        return InlineKeyboardButton(
            text=text,
            callback_data=DatePickerCallback(action="noop", value="-").pack(),
        )
    return InlineKeyboardButton(
        text=text,
        callback_data=DatePickerCallback(action="page", value=start.isoformat()).pack(),
    )
