"""Client appointment details, cancellation and reschedule controls."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.appointment import AppointmentView
from app.schemas.booking import BookingWindowView


class AppointmentCallback(CallbackData, prefix="appt"):
    """Client appointment action with optional date ordinal or window ID."""

    action: str
    appointment_id: int
    object_id: int


def appointment_list_keyboard(appointments: list[AppointmentView]) -> InlineKeyboardMarkup:
    rows = []
    for appointment in appointments:
        local = appointment.start_at.astimezone(ZoneInfo(appointment.timezone))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{local:%d.%m %H:%M} — {appointment.service_name}",
                    callback_data=AppointmentCallback(
                        action="view",
                        appointment_id=appointment.id,
                        object_id=0,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data=AppointmentCallback(
                    action="list",
                    appointment_id=0,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def appointment_details_keyboard(appointment: AppointmentView) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if appointment.can_self_manage:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="🔄 Перенести",
                        callback_data=AppointmentCallback(
                            action="reschedule",
                            appointment_id=appointment.id,
                            object_id=0,
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить",
                        callback_data=AppointmentCallback(
                            action="cancel_prompt",
                            appointment_id=appointment.id,
                            object_id=0,
                        ).pack(),
                    )
                ],
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="🖼 Референсы",
                    callback_data=AppointmentCallback(
                        action="references",
                        appointment_id=appointment.id,
                        object_id=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Добавить референс",
                    callback_data=AppointmentCallback(
                        action="references_add",
                        appointment_id=appointment.id,
                        object_id=0,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить референсы",
                    callback_data=AppointmentCallback(
                        action="references_clear_prompt",
                        appointment_id=appointment.id,
                        object_id=0,
                    ).pack(),
                ),
            ],
            [InlineKeyboardButton(text="Написать мастеру", url=appointment.master_telegram_url)],
            [InlineKeyboardButton(text="📍 Открыть на карте", url=appointment.map_url)],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку",
                    callback_data=AppointmentCallback(
                        action="list",
                        appointment_id=0,
                        object_id=0,
                    ).pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reference_edit_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Завершить",
                    callback_data=AppointmentCallback(
                        action="view",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ]
        ]
    )


def clear_references_confirmation_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить все",
                    callback_data=AppointmentCallback(
                        action="references_clear",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=AppointmentCallback(
                        action="view",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
        ]
    )


def cancel_confirmation_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отменить запись",
                    callback_data=AppointmentCallback(
                        action="cancel_confirm",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=AppointmentCallback(
                        action="view",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
        ]
    )


def reschedule_dates_keyboard(
    appointment_id: int,
    dates: list[date],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=local_date.strftime("%d.%m.%Y"),
                callback_data=AppointmentCallback(
                    action="rdate",
                    appointment_id=appointment_id,
                    object_id=local_date.toordinal(),
                ).pack(),
            )
        ]
        for local_date in dates
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К записи",
                callback_data=AppointmentCallback(
                    action="view",
                    appointment_id=appointment_id,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reschedule_windows_keyboard(
    appointment_id: int,
    windows: list[BookingWindowView],
) -> InlineKeyboardMarkup:
    rows = []
    for window in windows:
        local = window.start_at.astimezone(ZoneInfo(window.timezone))
        rows.append(
            [
                InlineKeyboardButton(
                    text=local.strftime("%H:%M"),
                    callback_data=AppointmentCallback(
                        action="rwindow",
                        appointment_id=appointment_id,
                        object_id=window.id,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К датам",
                callback_data=AppointmentCallback(
                    action="reschedule",
                    appointment_id=appointment_id,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reschedule_confirmation_keyboard(
    appointment_id: int,
    new_window_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, перенести",
                    callback_data=AppointmentCallback(
                        action="rconfirm",
                        appointment_id=appointment_id,
                        object_id=new_window_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=AppointmentCallback(
                        action="reschedule",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
        ]
    )
