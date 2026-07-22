"""Inline and reply controls for the client booking FSM."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.schemas.booking import BookingWindowView
from app.schemas.service import ServiceView

BOOKING_BACK_TEXT = "⬅️ Назад"
BOOKING_CANCEL_TEXT = "❌ Отменить оформление"


class BookingCallback(CallbackData, prefix="book"):
    """Compact action and numeric ID/ordinal for booking navigation."""

    action: str
    object_id: int


class BookingReferenceCallback(CallbackData, prefix="bref"):
    """Actions for the optional bounded reference-photo draft."""

    action: str


def services_keyboard(services: list[ServiceView]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{service.name} — {service.price:.2f} ₽",
                callback_data=BookingCallback(action="service", object_id=service.id).pack(),
            )
        ]
        for service in services
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=BOOKING_CANCEL_TEXT,
                callback_data=BookingCallback(action="cancel", object_id=0).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dates_keyboard(dates: list[date]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=local_date.strftime("%d.%m.%Y"),
                callback_data=BookingCallback(
                    action="date",
                    object_id=local_date.toordinal(),
                ).pack(),
            )
        ]
        for local_date in dates
    ]
    rows.extend(_inline_navigation("back_services"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def windows_keyboard(
    windows: list[BookingWindowView],
    local_date: date,
) -> InlineKeyboardMarkup:
    rows = []
    for window in windows:
        local = window.start_at.astimezone(ZoneInfo(window.timezone))
        rows.append(
            [
                InlineKeyboardButton(
                    text=local.strftime("%H:%M"),
                    callback_data=BookingCallback(
                        action="window",
                        object_id=window.id,
                    ).pack(),
                )
            ]
        )
    rows.extend(_inline_navigation("back_dates", local_date.toordinal()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=BookingCallback(action="confirm", object_id=0).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=BookingCallback(action="change", object_id=0).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=BOOKING_CANCEL_TEXT,
                    callback_data=BookingCallback(action="cancel", object_id=0).pack(),
                )
            ],
        ]
    )


def reference_media_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Готово",
                    callback_data=BookingReferenceCallback(action="done").pack(),
                ),
                InlineKeyboardButton(
                    text="⏭ Без фотографий",
                    callback_data=BookingReferenceCallback(action="skip").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить фотографии",
                    callback_data=BookingReferenceCallback(action="clear").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=BOOKING_BACK_TEXT,
                    callback_data=BookingReferenceCallback(action="back").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=BOOKING_CANCEL_TEXT,
                    callback_data=BookingReferenceCallback(action="cancel").pack(),
                )
            ],
        ]
    )


def booking_navigation_keyboard(*, request_contact: bool = False) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if request_contact:
        rows.append([KeyboardButton(text="📱 Отправить мой номер", request_contact=True)])
    rows.extend(
        [
            [KeyboardButton(text=BOOKING_BACK_TEXT)],
            [KeyboardButton(text=BOOKING_CANCEL_TEXT)],
        ]
    )
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def appointment_links_keyboard(map_url: str, master_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📍 Открыть на карте", url=map_url)],
            [InlineKeyboardButton(text="Написать мастеру", url=master_url)],
        ]
    )


def _inline_navigation(action: str, object_id: int = 0) -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                text=BOOKING_BACK_TEXT,
                callback_data=BookingCallback(action=action, object_id=object_id).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text=BOOKING_CANCEL_TEXT,
                callback_data=BookingCallback(action="cancel", object_id=0).pack(),
            )
        ],
    ]
