"""Self-scoped master workspace callbacks."""

from datetime import UTC, datetime

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import AppointmentStatus
from app.schemas.master_workspace import MasterAppointmentView
from app.schemas.payment import PaymentView


class MasterScheduleCallback(CallbackData, prefix="master_schedule"):
    action: str


class MasterAppointmentCallback(CallbackData, prefix="master_appt"):
    action: str
    appointment_id: int


class MasterPaymentCallback(CallbackData, prefix="master_pay"):
    action: str
    payment_id: int


def master_schedule_actions(*, is_paused: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Приостановить на 1 день",
                callback_data=MasterScheduleCallback(action="pause_1").pack(),
            ),
            InlineKeyboardButton(
                text="На 7 дней",
                callback_data=MasterScheduleCallback(action="pause_7").pack(),
            ),
        ]
    ]
    if is_paused:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Возобновить онлайн-запись",
                    callback_data=MasterScheduleCallback(action="resume").pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def master_appointment_actions(
    appointments: tuple[MasterAppointmentView, ...],
    *,
    now: datetime | None = None,
) -> InlineKeyboardMarkup | None:
    """Expose actions only when their time/state preconditions can currently succeed."""

    current = now or datetime.now(UTC)
    rows: list[list[InlineKeyboardButton]] = []
    active = {AppointmentStatus.CONFIRMED, AppointmentStatus.CLIENT_CONFIRMED}
    for item in appointments:
        if item.status not in active:
            continue
        if item.end_at <= current:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ Завершить №{item.appointment_id}",
                        callback_data=MasterAppointmentCallback(
                            action="request_complete",
                            appointment_id=item.appointment_id,
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text=f"🚫 Неявка №{item.appointment_id}",
                        callback_data=MasterAppointmentCallback(
                            action="request_no_show",
                            appointment_id=item.appointment_id,
                        ).pack(),
                    ),
                ]
            )
        elif item.start_at > current:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"❌ Отменить №{item.appointment_id}",
                        callback_data=MasterAppointmentCallback(
                            action="request_cancel",
                            appointment_id=item.appointment_id,
                        ).pack(),
                    )
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def master_appointment_confirmation(
    *,
    action: str,
    appointment_id: int,
) -> InlineKeyboardMarkup:
    labels = {
        "complete": "Да, завершить",
        "no_show": "Да, отметить неявку",
        "cancel": "Да, отменить",
    }
    if action not in labels:
        raise ValueError("Unsupported master appointment action")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=labels[action],
                    callback_data=MasterAppointmentCallback(
                        action=f"confirm_{action}",
                        appointment_id=appointment_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=MasterAppointmentCallback(
                        action="dismiss",
                        appointment_id=appointment_id,
                    ).pack(),
                ),
            ]
        ]
    )


def master_payment_actions(payments: tuple[PaymentView, ...]) -> InlineKeyboardMarkup | None:
    rows = [
        [
            InlineKeyboardButton(
                text=f"Подтвердить предоплату №{payment.id}",
                callback_data=MasterPaymentCallback(
                    action="approve_prompt",
                    payment_id=payment.id,
                ).pack(),
            )
        ]
        for payment in payments
        if payment.manual_status is not None
        and payment.manual_status.value in {"client_reported", "review_pending"}
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def master_payment_confirmation(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, деньги получены",
                    callback_data=MasterPaymentCallback(
                        action="approve_confirm",
                        payment_id=payment_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=MasterPaymentCallback(
                        action="dismiss",
                        payment_id=payment_id,
                    ).pack(),
                ),
            ]
        ]
    )
