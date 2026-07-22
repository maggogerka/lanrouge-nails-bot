"""Administrator review moderation without client-text editing."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.enums import ReviewModerationStatus
from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.main import ADMIN_REVIEWS_TEXT
from app.keyboards.admin.reviews import (
    AdminReviewCallback,
    admin_review_actions,
    admin_reviews_keyboard,
)
from app.schemas.pagination import PageRequest
from app.schemas.review import ReviewView
from app.services.review_service import ReviewService

router = Router(name="admin.reviews")


def _render(review: ReviewView) -> str:
    return (
        f"Отзыв #{review.id} · запись #{review.appointment_id}\n"
        f"Клиентка: {escape(review.client_name)}\n"
        f"Оценка: {review.rating}/5\n"
        f"Текст: {escape(review.text or 'без текста')}\n"
        f"Разрешение на публикацию: {'да' if review.publication_consent else 'нет'}\n"
        f"Статус: {review.moderation_status.value}"
    )


async def _list(message: Message, service: ReviewService) -> None:
    if message.from_user is None:
        return
    page = await service.list_admin(
        actor_from_telegram(message.from_user),
        status=ReviewModerationStatus.PENDING,
        page=PageRequest(page_size=20),
    )
    await message.answer(
        f"Новые отзывы: {page.total}", reply_markup=admin_reviews_keyboard(page.items)
    )


@router.message(F.text == ADMIN_REVIEWS_TEXT)
async def show_reviews(message: Message, review_service: ReviewService) -> None:
    await _list(message, review_service)


@router.callback_query(AdminReviewCallback.filter(F.action == "list"))
async def show_reviews_callback(callback: CallbackQuery, review_service: ReviewService) -> None:
    if isinstance(callback.message, Message):
        await _list(callback.message, review_service)
    await callback.answer()


@router.callback_query(AdminReviewCallback.filter(F.action == "view"))
async def show_review(
    callback: CallbackQuery,
    callback_data: AdminReviewCallback,
    review_service: ReviewService,
) -> None:
    page = await review_service.list_admin(
        actor_from_telegram(callback.from_user), page=PageRequest(page_size=50)
    )
    review = next((item for item in page.items if item.id == callback_data.review_id), None)
    if review is None:
        await callback.answer("Отзыв не найден.", show_alert=True)
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
