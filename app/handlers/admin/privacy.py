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

router = Router(name="admin.privacy")


def _render(request: DeletionRequestView) -> str:
    lines = [
        f"Запрос #{request.id}",
        f"Статус: {request.status.value}",
        f"Создан: {request.requested_at:%d.%m.%Y %H:%M UTC}",
    ]
    if request.result_code is not None:
        lines.append(f"Результат: {request.result_code}")
    if request.retention_reason_code is not None:
        lines.append(f"Причина хранения/отказа: {request.retention_reason_code}")
    return "\n".join(lines)


async def _show_list(message: Message, service: PrivacyDeletionRuntimeService) -> None:
    if message.from_user is None:
        return
    requests = await service.list_requests(actor_from_telegram(message.from_user))
    text = f"Запросы на удаление данных: {len(requests)}"
    await message.answer(text, reply_markup=deletion_requests_keyboard(requests))


@router.message(F.text == ADMIN_PRIVACY_TEXT)
async def show_deletion_requests(
    message: Message,
    privacy_deletion_service: PrivacyDeletionRuntimeService,
) -> None:
    try:
        await _show_list(message, privacy_deletion_service)
    except DomainError as exc:
        await message.answer(str(exc))


@router.callback_query(AdminDeletionCallback.filter(F.action == "list"))
async def show_deletion_requests_callback(
    callback: CallbackQuery,
    privacy_deletion_service: PrivacyDeletionRuntimeService,
) -> None:
    if isinstance(callback.message, Message):
        try:
            await _show_list(callback.message, privacy_deletion_service)
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
            actor_from_telegram(callback.from_user),
            callback_data.request_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _render(request),
            reply_markup=deletion_request_actions(request),
        )
    await callback.answer()


@router.callback_query(
    AdminDeletionCallback.filter(
        F.action.in_({"review_prompt", "approve_prompt", "execute_prompt"})
    )
)
async def prompt_deletion_transition(
    callback: CallbackQuery,
    callback_data: AdminDeletionCallback,
) -> None:
    prompts = {
        "review_prompt": "Подтвердить перевод запроса в работу?",
        "approve_prompt": "Подтвердить одобрение запроса? Данные ещё не изменятся.",
        "execute_prompt": (
            "Подтвердить необратимое обезличивание? История услуг и финансовые "
            "документы будут сохранены без прямых данных клиента."
        ),
    }
    actions = {
        "review_prompt": "review_confirm",
        "approve_prompt": "approve_confirm",
        "execute_prompt": "execute_confirm",
    }
    if isinstance(callback.message, Message):
        await callback.message.answer(
            prompts[callback_data.action],
            reply_markup=deletion_confirmation_keyboard(
                action=actions[callback_data.action],
                request_id=callback_data.request_id,
            ),
        )
    await callback.answer()


@router.callback_query(AdminDeletionCallback.filter(F.action == "reject_prompt"))
async def choose_rejection_reason(
    callback: CallbackQuery,
    callback_data: AdminDeletionCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Выберите обязательную причину отказа. Свободный текст не сохраняется.",
            reply_markup=deletion_rejection_reasons_keyboard(callback_data.request_id),
        )
    await callback.answer()


@router.callback_query(AdminDeletionCallback.filter(F.action == "reject_reason"))
async def confirm_rejection_reason(
    callback: CallbackQuery,
    callback_data: AdminDeletionCallback,
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
                actor,
                callback_data.request_id,
                confirmed=True,
                correlation_id=correlation_id,
            )
        elif callback_data.action == "approve_confirm":
            request = await privacy_deletion_service.approve(
                actor,
                callback_data.request_id,
                confirmed=True,
                correlation_id=correlation_id,
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
            _render(request),
            reply_markup=deletion_request_actions(request),
        )
    await callback.answer("Статус обновлён.")


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
            text = "Обезличивание завершено. Финансовые и сервисные снимки сохранены."
        else:
            text = (
                "Выполнение безопасно остановлено; запрос возвращён на проверку. "
                f"Коды: {', '.join(outcome.error_codes)}"
            )
        await callback.message.answer(
            f"{text}\n\n{_render(outcome.request)}",
            reply_markup=deletion_request_actions(outcome.request),
        )
    await callback.answer("Операция завершена." if outcome.completed else "Операция остановлена.")
