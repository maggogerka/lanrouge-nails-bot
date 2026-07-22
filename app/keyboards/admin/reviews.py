"""Review moderation buttons."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.review import ReviewView


class AdminReviewCallback(CallbackData, prefix="arev"):
    action: str
    review_id: int = 0


def admin_reviews_keyboard(reviews: list[ReviewView]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"#{review.id} · {review.rating}★ · {review.client_name}",
                    callback_data=AdminReviewCallback(action="view", review_id=review.id).pack(),
                )
            ]
            for review in reviews
        ]
        + [
            [
                InlineKeyboardButton(
                    text="🗑 Удалённые отзывы",
                    callback_data=AdminReviewCallback(action="deleted").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Обновить",
                    callback_data=AdminReviewCallback(action="list").pack(),
                )
            ],
        ]
    )


def admin_review_actions(review: ReviewView) -> InlineKeyboardMarkup:
    rows = []
    if review.deleted_at is not None:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="♻️ Восстановить",
                        callback_data=AdminReviewCallback(
                            action="restore", review_id=review.id
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⚠️ Удалить навсегда",
                        callback_data=AdminReviewCallback(
                            action="hard_prompt", review_id=review.id
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="← К удалённым",
                        callback_data=AdminReviewCallback(action="deleted").pack(),
                    )
                ],
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="✏️ Оценка",
                    callback_data=AdminReviewCallback(
                        action="edit_rating", review_id=review.id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="✏️ Текст",
                    callback_data=AdminReviewCallback(
                        action="edit_text", review_id=review.id
                    ).pack(),
                ),
            ]
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 Удалить",
                callback_data=AdminReviewCallback(action="delete", review_id=review.id).pack(),
            )
        ]
    )
    if review.publication_consent:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Опубликовать",
                    callback_data=AdminReviewCallback(action="approve", review_id=review.id).pack(),
                )
            ],
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=AdminReviewCallback(action="reject", review_id=review.id).pack(),
                ),
                InlineKeyboardButton(
                    text="Скрыть",
                    callback_data=AdminReviewCallback(action="hide", review_id=review.id).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← К отзывам",
                    callback_data=AdminReviewCallback(action="list").pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hard_delete_review_keyboard(review_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить безвозвратно",
                    callback_data=AdminReviewCallback(
                        action="hard_confirm", review_id=review_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=AdminReviewCallback(action="view", review_id=review_id).pack(),
                )
            ],
        ]
    )
