"""Tenant-scoped admin workflow for privacy deletion requests."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.main import ADMIN_PRIVACY_TEXT
from app.keyboards.admin.privacy import (
    AdminDeletionCallback,
    deletion_confirmation_keyboard,
    deletion_rejection_reasons_keyboard,
    deletion_request_actions,
    deletion_requests_keyboard,
)
from app.services.privacy_service import DeletionRequestView, PrivacyDeletionRuntimeService
from app.utils.pagination import paginate_sequence
from app.utils.telegram import edit_text_safely

router = Router(name="admin.privacy")

_STATUS_LABELS = {
    "requested": "новый",
    "in_review": "на проверке",
    "approved": "готов к обезличиванию",
    "processing": "обрабатывается",
    "failed": "ошибка — требуется безопасный повтор",
    "rejected": "отклонён",
    "completed": "завершён",
    "cancelled": "отменён",
}
_BLOCKER_LABELS = {
    "bootstrap_owner": "bootstrap-владелец не может быть обезличен",
    "active_staff_role.owner": "сначала отзовите активную роль владельца",
    "active_staff_role.manager": "сначала отзовите активную роль управляющего",
    "active_staff_role.master": "сначала отзовите активную роль мастера",
    "active_staff_role.receptionist": "сначала отзовите активную роль администратора",
    "active_staff_membership": "сначала отзовите активную роль сотрудника",
    "other_active_business_membership": "есть активный профиль в другом бизнесе",
    "anonymization_in_progress": "обезличивание уже выполняется",
    "anonymization_execution_failed": "безопасная попытка завершилась ошибкой",
    "anonymization_attempts_exhausted": "лимит автоматических попыток исчерпан",
}


def _render(request: DeletionRequestView) -> str:
    lines = [
        f"Запрос #{request.id}",
        f"Статус: {_STATUS_LABELS.get(request.status.value, request.status.value)}",
        f"Создан: {request.requested_at:%d.%m.%Y %H:%M UTC}",
        f"Попытки: {request.attempt_count}/{request.max_attempts}",
    ]
    if request.result_code is not None:
        lines.append(f"Результат: {request.result_code}")
    code = request.last_error_code or request.retention_reason_code
    if code is not None:
        lines.append(f"Причина: {_BLOCKER_LABELS.get(code, code)}")
    return "\n".join(lines)


async def _show_list(
    target: Message | CallbackQuery,
    service: PrivacyDeletionRuntimeService,
    *,
    page: int = 1,
) -> None:
    if target.from_user is None:
        return
    requests = await service.list_requests(actor_from_telegram(target.from_user))
    paged = paginate_sequence(requests, page=page, page_size=8)
    text = f"Запросы на удаление данных: {paged.total} · страница {paged.page} из {paged.pages}."
    keyboard = deletion_requests_keyboard(paged.items, page=paged.page, pages=paged.pages)
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            await edit_text_safely(target.message, text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(F.text == ADMIN_PRIVACY_TEXT)
async def show_deletion_requests(
    message: Message, privacy_deletion_service: PrivacyDeletionRuntimeService
) -> None:
    try:
        await _show_list(message, privacy_deletion_service)
    except DomainError as exc:
        await message.answer(str(exc))


@router.callback_query(AdminDeletionCallback.filter(F.action == "list"))
async def show_deletion_requests_callback(
    callback: CallbackQuery,
    callback_data: AdminDeletionCallback,
    privacy_deletion_service: PrivacyDeletionRuntimeService,
) -> None:
    try:
        await _show_list(
            callback,
            privacy_deletion_service,
            page=callback_data.request_id or 1,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()


@router.callback_query(AdminDeletionCallback.filter(F.action == "view"))
async def view_deletion_request(
    callback: CallbackQuery,
    callback_data: AdminDeletionCallback,
    privacy_deletion_service: PrivacyDeletionRuntimeService,
) -> None:
    try:
        request = await privacy_deletion_service.get_request(
            actor_from_telegram(callback.from_user), callback_data.request_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render(request), reply_markup=deletion_request_actions(request)
        )
    await callback.answer()


@router.callback_query(
    AdminDeletionCallback.filter(
        F.action.in_({"review_prompt", "approve_prompt", "execute_prompt", "retry_prompt"})
    )
)
async def prompt_deletion_transition(
    callback: CallbackQuery, callback_data: AdminDeletionCallback
) -> None:
    prompts = {
        "review_prompt": "Перевести запрос в работу?",
        "approve_prompt": "Одобрить запрос? Данные пока не изменятся.",
        "execute_prompt": (
            "Запустить необратимое обезличивание? Юридическая история услуг и финансов "
            "останется без прямых данных клиента."
        ),
        "retry_prompt": "Повторить обезличивание после проверки причины сбоя?",
    }
    actions = {
        "review_prompt": "review_confirm",
        "approve_prompt": "approve_confirm",
        "execute_prompt": "execute_confirm",
        "retry_prompt": "retry_confirm",
    }
    if isinstance(callback.message, Message):
        await callback.message.answer(
            prompts[callback_data.action],
            reply_markup=deletion_confirmation_keyboard(
                action=actions[callback_data.action], request_id=callback_data.request_id
            ),
        )
    await callback.answer()


@router.callback_query(AdminDeletionCallback.filter(F.action == "reject_prompt"))
async def choose_rejection_reason(
    callback: CallbackQuery, callback_data: AdminDeletionCallback
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Выберите обязательную причину отказа. Свободный текст не сохраняется.",
            reply_markup=deletion_rejection_reasons_keyboard(callback_data.request_id),
        )
    await callback.answer()


@router.callback_query(AdminDeletionCallback.filter(F.action == "reject_reason"))
async def confirm_rejection_reason(
    callback: CallbackQuery, callback_data: AdminDeletionCallback
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Подтвердить отказ с кодом {callback_data.reason_code}?",
            reply_markup=deletion_confirmation_keyboard(
                action="reject_confirm",
                request_id=callback_data.request_id,
                reason_code=callback_data.reason_code,
            ),
        )
    await callback.answer()


@router.callback_query(
    AdminDeletionCallback.filter(
        F.action.in_({"review_confirm", "approve_confirm", "reject_confirm"})
    )
)
async def apply_deletion_transition(
    callback: CallbackQuery,
    callback_data: AdminDeletionCallback,
    privacy_deletion_service: PrivacyDeletionRuntimeService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    try:
        if callback_data.action == "review_confirm":
            request = await privacy_deletion_service.start_review(
                actor, callback_data.request_id, confirmed=True, correlation_id=correlation_id
            )
        elif callback_data.action == "approve_confirm":
            request = await privacy_deletion_service.approve(
                actor, callback_data.request_id, confirmed=True, correlation_id=correlation_id
            )
        else:
            request = await privacy_deletion_service.reject(
                actor,
                callback_data.request_id,
                reason_code=callback_data.reason_code,
                confirmed=True,
                correlation_id=correlation_id,
            )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render(request), reply_markup=deletion_request_actions(request)
        )
    await callback.answer("Статус обновлён.")


@router.callback_query(AdminDeletionCallback.filter(F.action == "retry_confirm"))
async def retry_deletion_request(
    callback: CallbackQuery,
    callback_data: AdminDeletionCallback,
    privacy_deletion_service: PrivacyDeletionRuntimeService,
    correlation_id: str,
) -> None:
    try:
        request = await privacy_deletion_service.retry_failed(
            actor_from_telegram(callback.from_user),
            callback_data.request_id,
            confirmed=True,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render(request), reply_markup=deletion_request_actions(request)
        )
    await callback.answer("Повтор разрешён.")


@router.callback_query(AdminDeletionCallback.filter(F.action == "execute_confirm"))
async def execute_deletion_request(
    callback: CallbackQuery,
    callback_data: AdminDeletionCallback,
    privacy_deletion_service: PrivacyDeletionRuntimeService,
    correlation_id: str,
) -> None:
    try:
        outcome = await privacy_deletion_service.execute_anonymization(
            actor_from_telegram(callback.from_user),
            callback_data.request_id,
            confirmed=True,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        if outcome.completed:
            text = "Обезличивание завершено. Финансовая и сервисная история сохранена."
        else:
            reasons = ", ".join(_BLOCKER_LABELS.get(code, code) for code in outcome.error_codes)
            text = f"Обезличивание не выполнено: {reasons}."
        await callback.message.answer(
            f"{text}\n\n{_render(outcome.request)}",
            reply_markup=deletion_request_actions(outcome.request),
        )
    await callback.answer("Готово." if outcome.completed else "Операция остановлена безопасно.")
