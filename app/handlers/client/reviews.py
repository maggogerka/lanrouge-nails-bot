"""Client review form and moderated public review list."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.errors import DomainError
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.main import CLIENT_REVIEWS_TEXT
from app.keyboards.client.reviews import (
    ReviewCallback,
    public_reviews_keyboard,
    review_confirmation_keyboard,
    review_publication_keyboard,
    review_rating_keyboard,
    review_skip_text_keyboard,
)
from app.schemas.pagination import PageRequest
from app.schemas.review import ReviewCreate
from app.services.review_service import ReviewService
from app.states.review import ReviewFlow
from app.utils.telegram import edit_text_safely

router = Router(name="client.reviews")


async def _show_public_reviews(
    target: Message | CallbackQuery,
    review_service: ReviewService,
    *,
    page_number: int = 1,
) -> None:
    page = await review_service.list_public(PageRequest(page=page_number, page_size=1))
    if not page.items:
        message = target.message if isinstance(target, CallbackQuery) else target
        if isinstance(message, Message):
            await message.answer("Опубликованных отзывов пока нет.")
        return
    review = page.items[0]
    text = (
        f"<b>Отзыв {page.page} из {page.pages}</b>\n\n"
        f"{'⭐' * review.rating} · {escape(review.client_name)}\n"
        f"{escape(review.text or 'Без текста')}"
    )
    keyboard = public_reviews_keyboard(page=page.page, pages=page.pages)
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            await edit_text_safely(target.message, text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(F.text == CLIENT_REVIEWS_TEXT)
async def show_public_reviews(message: Message, review_service: ReviewService) -> None:
    await _show_public_reviews(message, review_service)


@router.callback_query(ReviewCallback.filter(F.action == "public_page"))
async def show_public_reviews_page(
    callback: CallbackQuery,
    callback_data: ReviewCallback,
    review_service: ReviewService,
) -> None:
    await _show_public_reviews(callback, review_service, page_number=callback_data.page)
    await callback.answer()


@router.callback_query(ReviewCallback.filter(F.action == "start"))
async def begin_review(
    callback: CallbackQuery,
    callback_data: ReviewCallback,
    state: FSMContext,
    review_service: ReviewService,
) -> None:
    try:
        await review_service.ensure_enabled()
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await state.update_data(appointment_id=callback_data.appointment_id)
    await state.set_state(ReviewFlow.rating)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Как вы оцените визит?",
            reply_markup=review_rating_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.callback_query(ReviewFlow.rating, ReviewCallback.filter(F.action == "rating"))
async def choose_review_rating(
    callback: CallbackQuery, callback_data: ReviewCallback, state: FSMContext
) -> None:
    if callback_data.value not in range(1, 6):
        await callback.answer("Выберите оценку от 1 до 5.", show_alert=True)
        return
    await state.update_data(rating=callback_data.value)
    await state.set_state(ReviewFlow.text)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Напишите несколько слов о визите или выберите «Без текста».",
            reply_markup=review_skip_text_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.message(ReviewFlow.text)
async def enter_review_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) > 2000:
        await message.answer("Текст отзыва должен быть не длиннее 2000 символов.")
        return
    data = await state.get_data()
    await state.update_data(text=text or None)
    await state.set_state(ReviewFlow.publication)
    await message.answer(
        "Можно ли опубликовать этот отзыв? Это отдельное согласие и оно не "
        "влияет на отправку отзыва мастеру.",
        reply_markup=review_publication_keyboard(int(data["appointment_id"])),
    )


@router.callback_query(ReviewFlow.text, ReviewCallback.filter(F.action == "skip_text"))
async def skip_review_text(
    callback: CallbackQuery, callback_data: ReviewCallback, state: FSMContext
) -> None:
    await state.update_data(text=None)
    await state.set_state(ReviewFlow.publication)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Можно ли опубликовать оценку?",
            reply_markup=review_publication_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.callback_query(ReviewFlow.publication, ReviewCallback.filter(F.action == "consent"))
async def choose_publication_consent(
    callback: CallbackQuery, callback_data: ReviewCallback, state: FSMContext
) -> None:
    await state.update_data(publication_consent=callback_data.value == 1)
    await state.set_state(ReviewFlow.confirmation)
    data = await state.get_data()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Оценка: {data['rating']}/5\n"
            f"Текст: {escape(str(data.get('text') or 'без текста'))}\n"
            f"Публикация: {'разрешена' if data['publication_consent'] else 'не разрешена'}",
            reply_markup=review_confirmation_keyboard(callback_data.appointment_id),
        )
    await callback.answer()


@router.callback_query(ReviewFlow.confirmation, ReviewCallback.filter(F.action == "confirm"))
async def submit_review(
    callback: CallbackQuery,
    callback_data: ReviewCallback,
    state: FSMContext,
    review_service: ReviewService,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    try:
        values = ReviewCreate(
            rating=int(data["rating"]),
            text=data.get("text"),
            publication_consent=bool(data["publication_consent"]),
        )
        await review_service.submit(
            actor_from_telegram(callback.from_user),
            callback_data.appointment_id,
            values,
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("Спасибо за отзыв! Он сохранён и передан мастеру.")
    await callback.answer()


@router.callback_query(ReviewCallback.filter(F.action == "cancel"))
async def cancel_review(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("Отзыв не отправлен.")
    await callback.answer()
