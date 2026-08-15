"""Administrator waitlist actions."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.waitlist import AdminWaitlistView


class AdminWaitlistCallback(CallbackData, prefix="awl"):
    action: str
    entry_id: int = 0
    page: int = 1


def admin_waitlist_keyboard(
    entries: list[AdminWaitlistView],
    *,
    page: int = 1,
    pages: int = 1,
    list_action: str = "active",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for entry in entries:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{entry.id} · {entry.client_name} · {entry.service_name}",
                    callback_data=AdminWaitlistCallback(
                        action="view", entry_id=entry.id, page=page
                    ).pack(),
                )
            ]
        )
    if pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=AdminWaitlistCallback(action=list_action, page=page - 1).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=AdminWaitlistCallback(action=list_action, page=page).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=AdminWaitlistCallback(action=list_action, page=page + 1).pack(),
                )
            )
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text="Активные", callback_data=AdminWaitlistCallback(action="active").pack()
            ),
            InlineKeyboardButton(
                text="Все", callback_data=AdminWaitlistCallback(action="all").pack()
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_waitlist_entry_keyboard(entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Написать клиенту",
                    callback_data=AdminWaitlistCallback(action="write", entry_id=entry_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Предложить окно",
                    callback_data=AdminWaitlistCallback(action="offer", entry_id=entry_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Архивировать",
                    callback_data=AdminWaitlistCallback(action="archive", entry_id=entry_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="← К списку", callback_data=AdminWaitlistCallback(action="active").pack()
                )
            ],
        ]
    )
