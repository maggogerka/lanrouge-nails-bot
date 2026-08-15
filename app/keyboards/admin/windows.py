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
    page: int = 1


def window_list_keyboard(
    windows: list[AvailabilityWindowView],
    *,
    include_archived: bool = False,
    page: int = 1,
    pages: int = 1,
) -> InlineKeyboardMarkup:
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
        master = f" · {window.master_name}" if window.master_name else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=(f"{status_markers[window.status]} {local:%d.%m %H:%M}{master}")[:64],
                    callback_data=WindowCallback(
                        action="view", window_id=window.id, page=page
                    ).pack(),
                )
            ]
        )
    list_action = "list_archived" if include_archived else "list"
    if pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=WindowCallback(
                        action=list_action, window_id=0, page=page - 1
                    ).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=WindowCallback(action=list_action, window_id=0, page=page).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=WindowCallback(
                        action=list_action, window_id=0, page=page + 1
                    ).pack(),
                )
            )
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text="🙈 Скрыть архив" if include_archived else "🗄 Показать архив",
                callback_data=WindowCallback(
                    action="list" if include_archived else "list_archived",
                    window_id=0,
                ).pack(),
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
                    text="Удалить, если нет записей",
                    callback_data=WindowCallback(
                        action="delete_confirm",
                        window_id=window_id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔥 Удалить вместе с записью",
                    callback_data=WindowCallback(
                        action="force_delete_prompt",
                        window_id=window_id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=WindowCallback(action="view", window_id=window_id).pack(),
                ),
            ],
        ]
    )


def force_delete_window_confirmation_keyboard(window_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔥 Да, удалить безвозвратно",
                    callback_data=WindowCallback(
                        action="force_delete_confirm",
                        window_id=window_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет, вернуться",
                    callback_data=WindowCallback(action="view", window_id=window_id).pack(),
                )
            ],
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
