"""Administrator review moderation without client-text editing."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import ReviewModerationStatus
from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.main import ADMIN_REVIEWS_TEXT
from app.keyboards.admin.reviews import (
    AdminReviewCallback,
    admin_review_actions,
    admin_reviews_keyboard,
    hard_delete_review_keyboard,
)
from app.keyboards.admin.services import cancel_keyboard
from app.schemas.pagination import PageRequest
from app.schemas.review import ReviewAdminUpdate, ReviewView
from app.services.review_service import ReviewService
from app.states.review import AdminReviewEdit

router = Router(name="admin.reviews")


def _render(review: ReviewView) -> str:
    return (
        f"Отзыв #{review.id} · запись #{review.appointment_id}\n"
        f"Клиентка: {escape(review.client_name)}\n"
        f"Оценка: {review.rating}/5\n"
        f"Текст: {escape(review.text or 'без текста')}\n"
        f"Разрешение на публикацию: {'да' if review.publication_consent else 'нет'}\n"
        f"Статус: {review.moderation_status.value}\n"
        f"Редактировался администратором: {'да' if review.is_admin_edited else 'нет'}\n"
        f"Удалён: {'да' if review.deleted_at else 'нет'}"
    )


async def _list(message: Message, service: ReviewService) -> None:
    if message.from_user is None:
        return
    page = await service.list_admin(
        actor_from_telegram(message.from_user),
        page=PageRequest(page_size=20),
    )
    await message.answer(f"Отзывы: {page.total}", reply_markup=admin_reviews_keyboard(page.items))


@router.message(F.text == ADMIN_REVIEWS_TEXT)
async def show_reviews(message: Message, review_service: ReviewService) -> None:
    await _list(message, review_service)


@router.callback_query(AdminReviewCallback.filter(F.action == "list"))
async def show_reviews_callback(callback: CallbackQuery, review_service: ReviewService) -> None:
    if isinstance(callback.message, Message):
        await _list(callback.message, review_service)
    await callback.answer()


@router.callback_query(AdminReviewCallback.filter(F.action == "deleted"))
async def show_deleted_reviews(
    callback: CallbackQuery,
    review_service: ReviewService,
) -> None:
    page = await review_service.list_admin(
        actor_from_telegram(callback.from_user),
        deleted_only=True,
        page=PageRequest(page_size=20),
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Удалённые отзывы: {page.total}",
            reply_markup=admin_reviews_keyboard(page.items),
        )
    await callback.answer()


@router.callback_query(AdminReviewCallback.filter(F.action == "view"))
async def show_review(
    callback: CallbackQuery,
    callback_data: AdminReviewCallback,
    review_service: ReviewService,
) -> None:
    try:
        review = await review_service.get_admin(
            actor_from_telegram(callback.from_user), callback_data.review_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(_render(review), reply_markup=admin_review_actions(review))
    await callback.answer()


@router.callback_query(AdminReviewCallback.filter(F.action.in_({"approve", "reject", "hide"})))
async def moderate_review(
    callback: CallbackQuery,
    callback_data: AdminReviewCallback,
    review_service: ReviewService,
    correlation_id: str,
) -> None:
    statuses = {
        "approve": ReviewModerationStatus.APPROVED,
        "reject": ReviewModerationStatus.REJECTED,
        "hide": ReviewModerationStatus.HIDDEN,
    }
    try:
        review = await review_service.moderate(
            actor_from_telegram(callback.from_user),
            callback_data.review_id,
            statuses[callback_data.action],
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(_render(review), reply_markup=admin_review_actions(review))
    await callback.answer("Статус отзыва обновлён.")


@router.callback_query(
    AdminReviewCallback.filter(F.action.in_({"edit_rating", "edit_text", "delete"}))
)
async def begin_review_edit(
    callback: CallbackQuery,
    callback_data: AdminReviewCallback,
    state: FSMContext,
) -> None:
    await state.set_data({"review_id": callback_data.review_id})
    prompts = {
        "edit_rating": (AdminReviewEdit.rating, "Введите новую оценку от 1 до 5:"),
        "edit_text": (
            AdminReviewEdit.text,
            "Введите новый текст до 2000 символов или «-», чтобы убрать текст:",
        ),
        "delete": (
            AdminReviewEdit.deletion_reason,
            "Укажите причину удаления до 500 символов:",
        ),
    }
    next_state, prompt = prompts[callback_data.action]
    await state.set_state(next_state)
    if isinstance(callback.message, Message):
        await callback.message.answer(prompt, reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AdminReviewEdit.rating)
async def save_review_rating(
    message: Message,
    state: FSMContext,
    review_service: ReviewService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    try:
        rating = int((message.text or "").strip())
        review = await review_service.edit_admin(
            actor_from_telegram(message.from_user),
            int(data["review_id"]),
            ReviewAdminUpdate(rating=rating),
            correlation_id=correlation_id,
        )
    except (DomainError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(_render(review), reply_markup=admin_review_actions(review))


@router.message(AdminReviewEdit.text)
async def save_review_text(
    message: Message,
    state: FSMContext,
    review_service: ReviewService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    raw = (message.text or "").strip()
    try:
        review = await review_service.edit_admin(
            actor_from_telegram(message.from_user),
            int(data["review_id"]),
            ReviewAdminUpdate(text=None if raw == "-" else raw),
            correlation_id=correlation_id,
        )
    except (DomainError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(_render(review), reply_markup=admin_review_actions(review))


@router.message(AdminReviewEdit.deletion_reason)
async def delete_review(
    message: Message,
    state: FSMContext,
    review_service: ReviewService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    try:
        review = await review_service.soft_delete(
            actor_from_telegram(message.from_user),
            int(data["review_id"]),
            message.text or "",
            correlation_id=correlation_id,
        )
    except (DomainError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(_render(review), reply_markup=admin_review_actions(review))


@router.callback_query(AdminReviewCallback.filter(F.action == "restore"))
async def restore_review(
    callback: CallbackQuery,
    callback_data: AdminReviewCallback,
    review_service: ReviewService,
    correlation_id: str,
) -> None:
    try:
        review = await review_service.restore(
            actor_from_telegram(callback.from_user),
            callback_data.review_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(_render(review), reply_markup=admin_review_actions(review))
    await callback.answer("Отзыв восстановлен.")


@router.callback_query(AdminReviewCallback.filter(F.action == "hard_prompt"))
async def prompt_hard_delete_review(
    callback: CallbackQuery,
    callback_data: AdminReviewCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Безвозвратно удалить отзыв и его ревизии? Это действие нельзя отменить.",
            reply_markup=hard_delete_review_keyboard(callback_data.review_id),
        )
    await callback.answer()


@router.callback_query(AdminReviewCallback.filter(F.action == "hard_confirm"))
async def hard_delete_review(
    callback: CallbackQuery,
    callback_data: AdminReviewCallback,
    review_service: ReviewService,
    correlation_id: str,
) -> None:
    try:
        await review_service.hard_delete(
            actor_from_telegram(callback.from_user),
            callback_data.review_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Отзыв безвозвратно удалён.")
    await callback.answer()
