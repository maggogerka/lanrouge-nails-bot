"""Client waitlist controls and booking offers."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.service import ServiceView
from app.schemas.waitlist import WaitlistView


class WaitlistCallback(CallbackData, prefix="wl"):
    action: str
    entry_id: int = 0
    object_id: int = 0


def waitlist_menu_keyboard(entries: list[WaitlistView]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="➕ Добавить запрос", callback_data=WaitlistCallback(action="add").pack()
            )
        ]
    ]
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"Отменить #{entry.id} · {entry.service_name}",
                callback_data=WaitlistCallback(action="cancel", entry_id=entry.id).pack(),
            )
        ]
        for entry in entries
        if entry.status.value in {"active", "matched"}
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def waitlist_services_keyboard(services: list[ServiceView]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=service.name,
                    callback_data=WaitlistCallback(action="service", object_id=service.id).pack(),
                )
            ]
            for service in services
        ]
    )


def waitlist_time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Любое время",
                    callback_data=WaitlistCallback(action="time", object_id=0).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Утро 09–13",
                    callback_data=WaitlistCallback(action="time", object_id=1).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="День 13–17",
                    callback_data=WaitlistCallback(action="time", object_id=2).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Вечер 17–21",
                    callback_data=WaitlistCallback(action="time", object_id=3).pack(),
                )
            ],
        ]
    )


def waitlist_offer_keyboard(entry_id: int, window_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Записаться на это время",
                    callback_data=WaitlistCallback(
                        action="book", entry_id=entry_id, object_id=window_id
                    ).pack(),
                )
            ]
        ]
    )
