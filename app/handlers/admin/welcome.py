"""Draft, preview and publication workflow for the public welcome message."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError
from app.keyboards.admin.business import (
    BusinessProfileCallback,
    BusinessWelcomeCallback,
    business_welcome_keyboard,
)
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.common.optional_input import is_optional_skip, optional_input_keyboard
from app.schemas.authorization import StaffContext
from app.schemas.business import BusinessWelcomeView
from app.services.business_service import BusinessAdministrationService
from app.states.business import BusinessWelcomeStates

router = Router(name="admin.welcome")


def _summary(view: BusinessWelcomeView) -> str:
    published = (
        view.published_at.strftime("%d.%m.%Y %H:%M UTC")
        if view.published_at is not None
        else "ещё не публиковалось"
    )
    return (
        "<b>Приветствие</b>\n"
        f"Фото в черновике: {'да' if view.draft_photo_file_id else 'нет'}\n"
        f"Опубликовано: {published}\n\n"
        "Новое содержимое не увидят клиенты, пока вы не нажмёте «Опубликовать»."
    )


async def _send_preview(message: Message, view: BusinessWelcomeView) -> None:
    if view.draft_photo_file_id is not None:
        await message.answer_photo(view.draft_photo_file_id)
    await message.answer(view.draft_text)


@router.callback_query(BusinessProfileCallback.filter(F.action == "welcome"))
async def show_welcome_settings(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get_welcome(staff_context)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _summary(view),
            reply_markup=business_welcome_keyboard(has_photo=view.draft_photo_file_id is not None),
        )
    await callback.answer()


@router.callback_query(BusinessWelcomeCallback.filter(F.action == "edit_text"))
async def begin_welcome_text(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BusinessWelcomeStates.waiting_text)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите текст до 3500 символов. Разрешены b, i, u, s, code, pre и безопасные "
            "HTTPS-ссылки. Отправьте «-», чтобы использовать стандартный текст.",
            reply_markup=optional_input_keyboard(),
        )
    await callback.answer()


@router.message(BusinessWelcomeStates.waiting_text)
async def save_welcome_text(
    message: Message,
    state: FSMContext,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Отправьте непустой текст или «-» для стандартного сообщения.")
        return
    try:
        view = await business_service.save_welcome_text(
            staff_context,
            None if is_optional_skip(raw) else raw,
            correlation_id=correlation_id,
        )
    except (DomainError, ValueError) as exc:
        await message.answer(f"Не удалось сохранить черновик: {escape(str(exc))}")
        return
    await state.clear()
    await message.answer(
        _summary(view),
        reply_markup=business_welcome_keyboard(has_photo=view.draft_photo_file_id is not None),
    )


@router.callback_query(BusinessWelcomeCallback.filter(F.action == "edit_photo"))
async def begin_welcome_photo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BusinessWelcomeStates.waiting_photo)
    if isinstance(callback.message, Message):
        await callback.message.answer("Отправьте одну фотографию.", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(BusinessWelcomeStates.waiting_photo, F.photo)
async def save_welcome_photo(
    message: Message,
    state: FSMContext,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    if not message.photo:
        return
    photo = message.photo[-1]
    view = await business_service.save_welcome_photo(
        staff_context,
        file_id=photo.file_id,
        file_unique_id=photo.file_unique_id,
        correlation_id=correlation_id,
    )
    await state.clear()
    await message.answer(_summary(view), reply_markup=business_welcome_keyboard(has_photo=True))


@router.message(BusinessWelcomeStates.waiting_photo)
async def reject_welcome_non_photo(message: Message) -> None:
    await message.answer("Нужно отправить фотографию.")


@router.callback_query(BusinessWelcomeCallback.filter(F.action == "preview"))
async def preview_welcome(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get_welcome(staff_context)
    if isinstance(callback.message, Message):
        await callback.message.answer("Предпросмотр черновика:")
        await _send_preview(callback.message, view)
    await callback.answer()


@router.callback_query(BusinessWelcomeCallback.filter(F.action == "remove_photo"))
async def remove_welcome_photo(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    view = await business_service.remove_welcome_photo(staff_context, correlation_id=correlation_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _summary(view), reply_markup=business_welcome_keyboard(has_photo=False)
        )
    await callback.answer("Фото удалено из черновика.")


@router.callback_query(BusinessWelcomeCallback.filter(F.action == "reset"))
async def reset_welcome(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    await business_service.reset_welcome_draft(staff_context, correlation_id=correlation_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "В черновике восстановлено стандартное сообщение. Проверьте и опубликуйте его.",
            reply_markup=business_welcome_keyboard(has_photo=False),
        )
    await callback.answer()


@router.callback_query(BusinessWelcomeCallback.filter(F.action == "publish"))
async def publish_welcome(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    view = await business_service.publish_welcome(staff_context, correlation_id=correlation_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _summary(view),
            reply_markup=business_welcome_keyboard(has_photo=view.draft_photo_file_id is not None),
        )
    await callback.answer("Приветствие опубликовано.", show_alert=True)
