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
    *,
    page: int = 1,
    pages: int = 1,
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
    if pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=AdminDeletionCallback(action="list", request_id=page - 1).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=AdminDeletionCallback(action="list", request_id=page).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=AdminDeletionCallback(action="list", request_id=page + 1).pack(),
                )
            )
        rows.append(navigation)
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
        rows.append([_button("Взять в работу", "review_prompt", request.id)])
    elif request.status is DataDeletionRequestStatus.IN_REVIEW:
        rows.append(
            [
                _button("Одобрить", "approve_prompt", request.id),
                _button("Отклонить", "reject_prompt", request.id),
            ]
        )
    elif request.status is DataDeletionRequestStatus.APPROVED:
        rows.append([_button("Выполнить обезличивание", "execute_prompt", request.id)])
    elif request.status is DataDeletionRequestStatus.FAILED:
        rows.append([_button("Безопасно повторить", "retry_prompt", request.id)])
    rows.append([_button("← К списку", "list", 0)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _button(text: str, action: str, request_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=AdminDeletionCallback(action=action, request_id=request_id).pack(),
    )


def deletion_confirmation_keyboard(
    *, action: str, request_id: int, reason_code: str = "none"
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
            [_button("Нет", "view", request_id)],
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
        + [[_button("Назад", "view", request_id)]]
    )
