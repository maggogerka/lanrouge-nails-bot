"""Client controls for the manual prepayment hand-off."""

from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import ManualPaymentStatus, PaymentMode, PaymentStatus
from app.schemas.payment import ClientPaymentSection, ClientPaymentView


class ManualPaymentCallback(CallbackData, prefix="mpay"):
    action: str
    payment_id: int


class ClientPaymentsCallback(CallbackData, prefix="cpay"):
    action: str
    payment_id: int = 0
    section: str = ClientPaymentSection.ACTIVE.value
    page: int = 1


def client_payments_home_keyboard(*, active_count: int, history_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⏳ Действующие · {active_count}",
                    callback_data=ClientPaymentsCallback(action="active").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🧾 История · {history_count}",
                    callback_data=ClientPaymentsCallback(action="history").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data=ClientPaymentsCallback(action="home").pack(),
                )
            ],
        ]
    )


def client_payments_list_keyboard(
    payments: list[ClientPaymentView],
    section: ClientPaymentSection,
    *,
    page: int,
    pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for payment in payments:
        local = payment.appointment_start_at.astimezone(ZoneInfo(payment.timezone))
        marker = "⏳" if section is ClientPaymentSection.ACTIVE else "🧾"
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{marker} {local:%d.%m %H:%M} · {payment.service_name} · "
                        f"{payment.amount:.2f} {payment.currency}"
                    )[:64],
                    callback_data=ClientPaymentsCallback(
                        action="view",
                        payment_id=payment.id,
                        section=section.value,
                        page=page,
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
                    callback_data=ClientPaymentsCallback(
                        action=section.value, section=section.value, page=page - 1
                    ).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=ClientPaymentsCallback(
                    action=section.value, section=section.value, page=page
                ).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=ClientPaymentsCallback(
                        action=section.value, section=section.value, page=page + 1
                    ).pack(),
                )
            )
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К разделу «Мои оплаты»",
                callback_data=ClientPaymentsCallback(action="home").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_payment_details_keyboard(
    payment: ClientPaymentView,
    *,
    section: ClientPaymentSection,
    page: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if (
        payment.provider is PaymentMode.MANUAL
        and payment.status is PaymentStatus.PENDING
        and payment.manual_status is ManualPaymentStatus.AWAITING_PAYMENT
    ):
        rows.append([manual_payment_report_button(payment.id)])
    if (
        payment.provider is PaymentMode.YOOKASSA
        and payment.status in {PaymentStatus.CREATED, PaymentStatus.PENDING}
        and payment.confirmation_url
    ):
        rows.append(
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment.confirmation_url)]
        )
    if (
        payment.provider is PaymentMode.MANUAL
        and payment.status is PaymentStatus.PENDING
        and payment.manual_status
        in {ManualPaymentStatus.CLIENT_REPORTED, ManualPaymentStatus.REVIEW_PENDING}
        and not payment.has_receipt
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text="📎 Прикрепить чек",
                    callback_data=ManualPaymentCallback(
                        action="attach", payment_id=payment.id
                    ).pack(),
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⬅️ К списку",
                    callback_data=ClientPaymentsCallback(
                        action=section.value, section=section.value, page=page
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Все оплаты",
                    callback_data=ClientPaymentsCallback(action="home").pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_payment_report_button(payment_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="✅ Я оплатил",
        callback_data=ManualPaymentCallback(action="paid", payment_id=payment_id).pack(),
    )


def receipt_choice_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📎 Прикрепить чек",
                    callback_data=ManualPaymentCallback(
                        action="attach", payment_id=payment_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=ManualPaymentCallback(
                        action="skip", payment_id=payment_id
                    ).pack(),
                )
            ],
        ]
    )
