"""Client waitlist controls and booking offers."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.service import ServiceView
from app.schemas.waitlist import WaitlistView
from app.utils.pagination import paginate_sequence


class WaitlistCallback(CallbackData, prefix="wl"):
    action: str
    entry_id: int = 0
    object_id: int = 0
    page: int = 1


def waitlist_menu_keyboard(
    entries: list[WaitlistView], *, page: int = 1, pages: int = 1
) -> InlineKeyboardMarkup:
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
    if pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=WaitlistCallback(action="list", page=page - 1).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=WaitlistCallback(action="list", page=page).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=WaitlistCallback(action="list", page=page + 1).pack(),
                )
            )
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def waitlist_services_keyboard(
    services: list[ServiceView], *, page: int = 1
) -> InlineKeyboardMarkup:
    paged = paginate_sequence(services, page=page, page_size=8)
    rows = [
        [
            InlineKeyboardButton(
                text=service.name,
                callback_data=WaitlistCallback(action="service", object_id=service.id).pack(),
            )
        ]
        for service in paged.items
    ]
    if paged.pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if paged.page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=WaitlistCallback(
                        action="service_page", page=paged.page - 1
                    ).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{paged.page}/{paged.pages}",
                callback_data=WaitlistCallback(action="service_page", page=paged.page).pack(),
            )
        )
        if paged.page < paged.pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=WaitlistCallback(
                        action="service_page", page=paged.page + 1
                    ).pack(),
                )
            )
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
