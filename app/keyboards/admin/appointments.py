"""Authorized appointment administration callbacks and keyboards."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import AppointmentStatus
from app.schemas.appointment import AdminAppointmentView
from app.schemas.booking import BookingWindowView


class AdminAppointmentCallback(CallbackData, prefix="aapt"):
    action: str
    appointment_id: int
    object_id: int


_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_STATUS_MARKERS = {
    AppointmentStatus.PENDING_PAYMENT: "⏳",
    AppointmentStatus.PENDING_MANUAL_CONFIRMATION: "💳",
    AppointmentStatus.CONFIRMED: "✅",
    AppointmentStatus.CLIENT_CONFIRMED: "👍",
}


def admin_appointment_list_keyboard(
    appointments: list[AdminAppointmentView],
    *,
    list_action: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current_date: date | None = None
    sorted_appointments = sorted(appointments, key=lambda item: (item.start_at, item.id))
    date_counts: dict[date, int] = {}
    for appointment in sorted_appointments:
        local_date = appointment.start_at.astimezone(ZoneInfo(appointment.timezone)).date()
        date_counts[local_date] = date_counts.get(local_date, 0) + 1

    for appointment in sorted_appointments:
        local = appointment.start_at.astimezone(ZoneInfo(appointment.timezone))
        if local.date() != current_date:
            current_date = local.date()
            count = date_counts[current_date]
            rows.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"📅 {_WEEKDAYS[current_date.weekday()]}, {current_date.day} "
                            f"{_MONTHS[current_date.month - 1]} · {count} "
                            f"{_appointments_word(count)}"
                        ),
                        callback_data=AdminAppointmentCallback(
                            action="day_label",
                            appointment_id=0,
                            object_id=current_date.toordinal(),
                        ).pack(),
                    )
                ]
            )
        marker = _STATUS_MARKERS.get(appointment.status, "•")
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{marker} {local:%H:%M} · "
                        f"{appointment.master_name or 'мастер не указан'} · "
                        f"{appointment.client_name} "
                        f"· {appointment.service_name}"
                    )[:64],
                    callback_data=AdminAppointmentCallback(
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
                text="🔄 Обновить календарь",
                callback_data=AdminAppointmentCallback(
                    action=list_action,
                    appointment_id=0,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _appointments_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "запись"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return "записи"
    return "записей"


def admin_appointment_details_keyboard(
    appointment: AdminAppointmentView,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    contact_url = (
        f"https://t.me/{appointment.client_username}"
        if appointment.client_username
        else (
            f"tg://user?id={appointment.client_telegram_id}"
            if appointment.client_telegram_id
            else None
        )
    )
    if contact_url:
        rows.append([InlineKeyboardButton(text="💬 Написать клиенту", url=contact_url)])
    if appointment.status.value == "confirmed":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить визит",
                    callback_data=AdminAppointmentCallback(
                        action="confirm",
                        appointment_id=appointment.id,
                        object_id=0,
                    ).pack(),
                )
            ]
        )
    if appointment.status.value in {"confirmed", "client_confirmed"}:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="Завершить визит",
                        callback_data=AdminAppointmentCallback(
                            action="complete",
                            appointment_id=appointment.id,
                            object_id=0,
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🚫 Отметить неявку",
                        callback_data=AdminAppointmentCallback(
                            action="no_show",
                            appointment_id=appointment.id,
                            object_id=0,
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Перенести",
                        callback_data=AdminAppointmentCallback(
                            action="reschedule",
                            appointment_id=appointment.id,
                            object_id=0,
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить",
                        callback_data=AdminAppointmentCallback(
                            action="cancel_prompt",
                            appointment_id=appointment.id,
                            object_id=0,
                        ).pack(),
                    )
                ],
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🖼 Референсы",
                callback_data=AdminAppointmentCallback(
                    action="references",
                    appointment_id=appointment.id,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 Удалить референсы",
                callback_data=AdminAppointmentCallback(
                    action="references_delete_prompt",
                    appointment_id=appointment.id,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К ближайшим",
                callback_data=AdminAppointmentCallback(
                    action="upcoming",
                    appointment_id=0,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_cancel_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отменить и открыть окно",
                    callback_data=AdminAppointmentCallback(
                        action="cancel_open",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отменить и закрыть окно",
                    callback_data=AdminAppointmentCallback(
                        action="cancel_close",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=AdminAppointmentCallback(
                        action="view",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
        ]
    )


def admin_reference_delete_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить референсы",
                    callback_data=AdminAppointmentCallback(
                        action="references_delete_confirm",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет, оставить",
                    callback_data=AdminAppointmentCallback(
                        action="view",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
        ]
    )


def admin_reschedule_dates_keyboard(
    appointment_id: int,
    dates: list[date],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=local_date.strftime("%d.%m.%Y"),
                callback_data=AdminAppointmentCallback(
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
                text="Назад",
                callback_data=AdminAppointmentCallback(
                    action="view",
                    appointment_id=appointment_id,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_reschedule_windows_keyboard(
    appointment_id: int,
    windows: list[BookingWindowView],
) -> InlineKeyboardMarkup:
    rows = []
    for window in windows:
        local = window.start_at.astimezone(ZoneInfo(window.timezone))
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{local:%H:%M} · {window.master_name}"
                        if window.master_name
                        else local.strftime("%H:%M")
                    )[:64],
                    callback_data=AdminAppointmentCallback(
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
                text="К датам",
                callback_data=AdminAppointmentCallback(
                    action="reschedule",
                    appointment_id=appointment_id,
                    object_id=0,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_reschedule_confirmation_keyboard(
    appointment_id: int,
    new_window_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, перенести",
                    callback_data=AdminAppointmentCallback(
                        action="rconfirm",
                        appointment_id=appointment_id,
                        object_id=new_window_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=AdminAppointmentCallback(
                        action="reschedule",
                        appointment_id=appointment_id,
                        object_id=0,
                    ).pack(),
                )
            ],
        ]
    )
