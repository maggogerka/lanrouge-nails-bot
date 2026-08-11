"""Inline controls for manual availability windows."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import AvailabilityWindowStatus
from app.schemas.availability import AvailabilityWindowView


class WindowCallback(CallbackData, prefix="win"):
    """Compact window management callback payload."""

    action: str
    window_id: int


def window_list_keyboard(windows: list[AvailabilityWindowView]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    status_markers = {
        AvailabilityWindowStatus.OPEN: "✅",
        AvailabilityWindowStatus.RESERVED: "⏳",
        AvailabilityWindowStatus.BOOKED: "🔒",
        AvailabilityWindowStatus.CLOSED: "⏸",
        AvailabilityWindowStatus.EXPIRED: "⌛",
    }
    for window in windows:
        local = window.start_at.astimezone(ZoneInfo(window.timezone))
        rows.append(
            [
                InlineKeyboardButton(
                    text=(f"{status_markers[window.status]} {local:%d.%m %H:%M}"),
                    callback_data=WindowCallback(action="view", window_id=window.id).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить окно",
                callback_data=WindowCallback(action="add", window_id=0).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def window_details_keyboard(window: AvailabilityWindowView) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if window.status is AvailabilityWindowStatus.OPEN:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⏸ Закрыть",
                    callback_data=WindowCallback(action="close", window_id=window.id).pack(),
                )
            ]
        )
    elif window.status is AvailabilityWindowStatus.CLOSED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="▶️ Открыть снова",
                    callback_data=WindowCallback(action="reopen", window_id=window.id).pack(),
                )
            ]
        )
    if window.status not in {
        AvailabilityWindowStatus.RESERVED,
        AvailabilityWindowStatus.BOOKED,
    }:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=WindowCallback(
                        action="delete_prompt",
                        window_id=window.id,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К списку",
                callback_data=WindowCallback(action="list", window_id=0).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def window_status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Открыто",
                    callback_data=WindowCallback(action="status_open", window_id=0).pack(),
                ),
                InlineKeyboardButton(
                    text="⏸ Закрыто",
                    callback_data=WindowCallback(action="status_closed", window_id=0).pack(),
                ),
            ]
        ]
    )


def delete_window_confirmation_keyboard(window_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=WindowCallback(
                        action="delete_confirm",
                        window_id=window_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=WindowCallback(action="view", window_id=window_id).pack(),
                ),
            ]
        ]
    )


def stale_window_keyboard() -> InlineKeyboardMarkup:
    """Offer a recoverable destination for callbacks from an expired draft."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ К открытым окнам",
                    callback_data=WindowCallback(action="list", window_id=0).pack(),
                )
            ]
        ]
    )
