"""PII-free administration controls for data-deletion requests."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import DataDeletionRequestStatus
from app.services.privacy_service import DeletionRequestView


class AdminDeletionCallback(CallbackData, prefix="adel"):
    action: str
    request_id: int = 0
    reason_code: str = "none"


def deletion_requests_keyboard(
    requests: tuple[DeletionRequestView, ...],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"#{item.id} · {item.status.value} · {item.requested_at:%d.%m.%Y}",
                callback_data=AdminDeletionCallback(action="view", request_id=item.id).pack(),
            )
        ]
        for item in requests
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data=AdminDeletionCallback(action="list").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deletion_request_actions(request: DeletionRequestView) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if request.status is DataDeletionRequestStatus.REQUESTED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Взять в работу",
                    callback_data=AdminDeletionCallback(
                        action="review_prompt", request_id=request.id
                    ).pack(),
                )
            ]
        )
    elif request.status is DataDeletionRequestStatus.IN_REVIEW:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Одобрить",
                    callback_data=AdminDeletionCallback(
                        action="approve_prompt", request_id=request.id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=AdminDeletionCallback(
                        action="reject_prompt", request_id=request.id
                    ).pack(),
                ),
            ]
        )
    elif request.status is DataDeletionRequestStatus.APPROVED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Выполнить обезличивание",
                    callback_data=AdminDeletionCallback(
                        action="execute_prompt", request_id=request.id
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="← К списку",
                callback_data=AdminDeletionCallback(action="list").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deletion_confirmation_keyboard(
    *,
    action: str,
    request_id: int,
    reason_code: str = "none",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, подтверждаю",
                    callback_data=AdminDeletionCallback(
                        action=action,
                        request_id=request_id,
                        reason_code=reason_code,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=AdminDeletionCallback(
                        action="view", request_id=request_id
                    ).pack(),
                )
            ],
        ]
    )


def deletion_rejection_reasons_keyboard(request_id: int) -> InlineKeyboardMarkup:
    reasons = (
        ("Не подтверждена личность", "identity_not_verified"),
        ("Обязательное хранение", "legal_retention_required"),
        ("Активный сотрудник", "active_staff_membership"),
        ("Активен в другом бизнесе", "other_active_business_membership"),
        ("Запрос некорректен", "request_invalid"),
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=AdminDeletionCallback(
                        action="reject_reason",
                        request_id=request_id,
                        reason_code=code,
                    ).pack(),
                )
            ]
            for label, code in reasons
        ]
        + [
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=AdminDeletionCallback(
                        action="view", request_id=request_id
                    ).pack(),
                )
            ]
        ]
    )
