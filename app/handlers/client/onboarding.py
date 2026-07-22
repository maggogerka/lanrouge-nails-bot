"""Explicit privacy and optional marketing onboarding."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.consent import (
    ConsentCallback,
    deletion_request_keyboard,
    marketing_consent_keyboard,
    privacy_consent_keyboard,
)
from app.keyboards.client.main import client_main_keyboard
from app.services.consent_service import ConsentService

router = Router(name="client.onboarding")

_PRIVACY_TEXT = (
    "Добро пожаловать в <b>lanrouge nails</b>! 💅\n\n"
    "Для записи бот хранит только необходимые контактные данные, согласия и историю "
    "визитов. Ознакомьтесь с политикой и явно подтвердите согласие на обработку данных."
)
_MARKETING_TEXT = (
    "Хотите отдельно получать рекламные сообщения о новых дизайнах и свободных окнах? "
    "Отказ не отключает подтверждения записи и сервисные напоминания."
)


@router.message(CommandStart())
async def handle_start(
    message: Message,
    state: FSMContext,
    consent_service: ConsentService,
    settings: Settings,
) -> None:
    if message.from_user is None:
        return
    await state.clear()
    status = await consent_service.get_or_create_status(actor_from_telegram(message.from_user))
    if not status.privacy_accepted:
        if settings.privacy_policy_url is None:
            await message.answer(
                "Добро пожаловать в <b>lanrouge nails</b>! 💅\n\n"
                "Онлайн-запись временно недоступна: владелец ещё не опубликовал политику "
                "конфиденциальности. Команда /whoami продолжает работать."
            )
            return
        await message.answer(
            _PRIVACY_TEXT,
            reply_markup=privacy_consent_keyboard(str(settings.privacy_policy_url)),
        )
        return
    if not status.marketing_answered:
        await message.answer(_MARKETING_TEXT, reply_markup=marketing_consent_keyboard())
        return
    await message.answer(
        "С возвращением в <b>lanrouge nails</b>!",
        reply_markup=client_main_keyboard(),
    )


@router.callback_query(ConsentCallback.filter(F.action == "privacy_accept"))
async def accept_privacy(
    callback: CallbackQuery,
    consent_service: ConsentService,
    correlation_id: str,
) -> None:
    await consent_service.accept_privacy(
        actor_from_telegram(callback.from_user),
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Согласие на обработку данных сохранено.")
        await callback.message.answer(
            _MARKETING_TEXT,
            reply_markup=marketing_consent_keyboard(),
        )
    await callback.answer()


@router.callback_query(
    ConsentCallback.filter(F.action.in_({"marketing_accept", "marketing_decline"}))
)
async def choose_marketing(
    callback: CallbackQuery,
    callback_data: ConsentCallback,
    consent_service: ConsentService,
    correlation_id: str,
) -> None:
    accepted = callback_data.action == "marketing_accept"
    await consent_service.set_marketing(
        actor_from_telegram(callback.from_user),
        accepted=accepted,
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Рекламная подписка включена."
            if accepted
            else "Рекламная подписка не включена. Сервисные сообщения останутся доступны."
        )
        await callback.message.answer(
            "Можно переходить к записи.",
            reply_markup=client_main_keyboard(),
        )
    await callback.answer()


@router.message(Command("delete_my_data"))
async def request_data_deletion(message: Message) -> None:
    await message.answer(
        "Отправить мастеру запрос на удаление или допустимую анонимизацию ваших данных? "
        "История записей может сохраняться в обезличенном виде в пределах обязательных сроков.",
        reply_markup=deletion_request_keyboard(),
    )


@router.callback_query(ConsentCallback.filter(F.action == "deletion_confirm"))
async def confirm_data_deletion_request(
    callback: CallbackQuery,
    consent_service: ConsentService,
    correlation_id: str,
) -> None:
    await consent_service.request_deletion(
        actor_from_telegram(callback.from_user),
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Запрос сохранён. Рекламная подписка отключена. Мастер свяжется с вами "
            "для подтверждения допустимого состава удаления или анонимизации."
        )
    await callback.answer("Запрос отправлен.")


@router.callback_query(ConsentCallback.filter(F.action == "deletion_cancel"))
async def cancel_data_deletion_request(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Запрос на удаление данных отменён.")
    await callback.answer()
