"""Explicit payment review and manual approval controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import ManualPaymentStatus, PaymentMode, PaymentStatus
from app.schemas.payment import PaymentView, RefundView


class PaymentAdminCallback(CallbackData, prefix="payadm"):
    action: str
    payment_id: int = 0
    mode: str = "none"


def payments_keyboard(
    payments: tuple[PaymentView, ...],
    *,
    can_manage: bool,
    can_reject: bool = False,
    can_refund: bool = False,
    can_edit_instructions: bool = False,
    can_edit_timers: bool = False,
    can_change_settings: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for payment in payments:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"#{payment.id} · {payment.amount:.2f} {payment.currency} · "
                        f"{payment.status.value}"
                    ),
                    callback_data=PaymentAdminCallback(
                        action="view",
                        payment_id=payment.id,
                    ).pack(),
                )
            ]
        )
        if (
            can_manage
            and payment.provider is PaymentMode.MANUAL
            and payment.status is PaymentStatus.PENDING
            and payment.manual_status
            in {ManualPaymentStatus.CLIENT_REPORTED, ManualPaymentStatus.REVIEW_PENDING}
        ):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Подтвердить ручную оплату #{payment.id}",
                        callback_data=PaymentAdminCallback(
                            action="approve_prompt",
                            payment_id=payment.id,
                        ).pack(),
                    )
                ]
            )
        if (
            can_reject
            and payment.provider is PaymentMode.MANUAL
            and payment.status is PaymentStatus.PENDING
            and payment.manual_status
            in {ManualPaymentStatus.CLIENT_REPORTED, ManualPaymentStatus.REVIEW_PENDING}
        ):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Отклонить ручную оплату #{payment.id}",
                        callback_data=PaymentAdminCallback(
                            action="reject_prompt", payment_id=payment.id
                        ).pack(),
                    )
                ]
            )
        if can_refund and payment.status in {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.PARTIALLY_REFUNDED,
        }:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Оформить возврат остатка #{payment.id}",
                        callback_data=PaymentAdminCallback(
                            action="refund_prompt",
                            payment_id=payment.id,
                        ).pack(),
                    )
                ]
            )
    if can_edit_instructions:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Настроить инструкцию ручной оплаты",
                    callback_data=PaymentAdminCallback(action="edit_manual_instructions").pack(),
                )
            ]
        )
    if can_edit_timers:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Настроить таймер оплаты",
                    callback_data=PaymentAdminCallback(action="edit_payment_timer").pack(),
                )
            ]
        )
    if can_change_settings:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="Режим: без предоплаты",
                        callback_data=PaymentAdminCallback(
                            action="mode_prompt",
                            mode=PaymentMode.DISABLED.value,
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text="Режим: вручную",
                        callback_data=PaymentAdminCallback(
                            action="mode_prompt",
                            mode=PaymentMode.MANUAL.value,
                        ).pack(),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Режим: YooKassa",
                        callback_data=PaymentAdminCallback(
                            action="mode_prompt",
                            mode=PaymentMode.YOOKASSA.value,
                        ).pack(),
                    )
                ],
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data=PaymentAdminCallback(action="list").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_approval_confirmation(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, деньги получены",
                    callback_data=PaymentAdminCallback(
                        action="approve_confirm",
                        payment_id=payment_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=PaymentAdminCallback(action="list").pack(),
                )
            ],
        ]
    )


def manual_rejection_reason_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без комментария",
                    callback_data=PaymentAdminCallback(
                        action="reject_no_reason", payment_id=payment_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=PaymentAdminCallback(action="list").pack(),
                )
            ],
        ]
    )


def manual_instruction_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сохранить инструкцию",
                    callback_data=PaymentAdminCallback(action="instructions_save").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Изменить текст",
                    callback_data=PaymentAdminCallback(action="edit_manual_instructions").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=PaymentAdminCallback(action="list").pack(),
                )
            ],
        ]
    )


def refund_confirmation(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, вернуть весь остаток",
                    callback_data=PaymentAdminCallback(
                        action="refund_confirm",
                        payment_id=payment_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=PaymentAdminCallback(action="list").pack(),
                )
            ],
        ]
    )


def manual_refund_confirmation(refund: RefundView) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, деньги возвращены",
                    callback_data=PaymentAdminCallback(
                        action="manual_refund_confirm",
                        payment_id=refund.id,
                    ).pack(),
                )
            ]
        ]
    )


def payment_mode_confirmation(mode: PaymentMode) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, изменить режим",
                    callback_data=PaymentAdminCallback(
                        action="mode_confirm",
                        mode=mode.value,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=PaymentAdminCallback(action="list").pack(),
                )
            ],
        ]
    )
