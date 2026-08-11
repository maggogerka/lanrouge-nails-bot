"""Explicit privacy and optional marketing onboarding."""

from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import User as TelegramUser

from app.domain.acquisition import CampaignValidationError, validate_campaign_code
from app.domain.enums import StaffRole
from app.domain.errors import DomainError
from app.domain.legal import MARKETING_CONSENT_TEXT
from app.filters import IsStaff
from app.handlers.client.common import actor_from_telegram
from app.handlers.client.portfolio import show_deep_linked_portfolio_item
from app.keyboards.client.consent import (
    ConsentCallback,
    deletion_request_keyboard,
    marketing_consent_keyboard,
    privacy_consent_keyboard,
)
from app.keyboards.client.main import client_main_keyboard
from app.keyboards.common.interface_mode import (
    InterfaceModeCallback,
    return_to_management_keyboard,
)
from app.schemas.authorization import StaffIdentity
from app.schemas.booking import ClientActor
from app.schemas.features import FeatureName
from app.services.acquisition_service import AcquisitionRuntimeService
from app.services.authorization_service import AuthorizationService
from app.services.consent_service import ConsentService
from app.services.feature_flag_service import FeatureFlagService
from app.services.menu_service import MenuService
from app.services.portfolio_service import PortfolioService
from app.services.presentation_service import PresentationService
from app.services.privacy_service import DeletionRequestNotificationService

router = Router(name="client.onboarding")
_PENDING_ACQUISITION_KEY = "pending_acquisition_code"
_ALL_STAFF_ROLES = frozenset(StaffRole)


def privacy_text(business_name: str) -> str:
    return (
        f"Добро пожаловать в <b>{escape(business_name)}</b>! 💅\n\n"
        "Для записи бот хранит только необходимые контактные данные, согласия и историю "
        "визитов. Ознакомьтесь с политикой и явно подтвердите согласие на обработку данных."
    )


@router.message(CommandStart())
async def handle_start(
    message: Message,
    state: FSMContext,
    consent_service: ConsentService,
    portfolio_service: PortfolioService,
    bot: Bot,
    menu_service: MenuService,
    presentation_service: PresentationService,
    feature_flag_service: FeatureFlagService,
    acquisition_service: AcquisitionRuntimeService,
    authorization_service: AuthorizationService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    await state.clear()
    payload = (message.text or "").split(maxsplit=1)
    start_payload = payload[1] if len(payload) == 2 else None
    if start_payload is not None and start_payload.startswith("staff_"):
        await _accept_staff_invitation(
            message,
            authorization_service,
            start_payload.removeprefix("staff_"),
            correlation_id=correlation_id,
        )
        return
    await _show_client_entry(
        message,
        message.from_user,
        start_payload=start_payload,
        state=state,
        consent_service=consent_service,
        portfolio_service=portfolio_service,
        bot=bot,
        menu_service=menu_service,
        presentation_service=presentation_service,
        feature_flag_service=feature_flag_service,
        acquisition_service=acquisition_service,
    )


@router.callback_query(
    InterfaceModeCallback.filter(F.action == "client"),
    IsStaff(allowed_roles=_ALL_STAFF_ROLES),
)
async def open_client_interface_for_staff(
    callback: CallbackQuery,
    state: FSMContext,
    consent_service: ConsentService,
    portfolio_service: PortfolioService,
    bot: Bot,
    menu_service: MenuService,
    presentation_service: PresentationService,
    feature_flag_service: FeatureFlagService,
    acquisition_service: AcquisitionRuntimeService,
) -> None:
    """Render the genuine client flow while retaining server-side staff access."""

    await state.clear()
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.message.answer(
        "Вы смотрите бот глазами клиента.",
        reply_markup=return_to_management_keyboard(),
    )
    await _show_client_entry(
        callback.message,
        callback.from_user,
        start_payload=None,
        state=state,
        consent_service=consent_service,
        portfolio_service=portfolio_service,
        bot=bot,
        menu_service=menu_service,
        presentation_service=presentation_service,
        feature_flag_service=feature_flag_service,
        acquisition_service=acquisition_service,
    )
    await callback.answer()


async def _show_client_entry(
    message: Message,
    telegram_user: TelegramUser,
    *,
    start_payload: str | None,
    state: FSMContext,
    consent_service: ConsentService,
    portfolio_service: PortfolioService,
    bot: Bot,
    menu_service: MenuService,
    presentation_service: PresentationService,
    feature_flag_service: FeatureFlagService,
    acquisition_service: AcquisitionRuntimeService,
) -> None:
    """Show the same consent-aware client surface to clients and authorized staff."""

    campaign_code = _campaign_code(start_payload)
    if campaign_code is not None:
        await state.update_data({_PENDING_ACQUISITION_KEY: campaign_code})
    try:
        business = await presentation_service.get_business()
    except DomainError:
        await message.answer("Бот временно недоступен: профиль бизнеса не настроен.")
        return
    actor = actor_from_telegram(telegram_user)
    status = await consent_service.get_or_create_status(actor)
    if not status.privacy_accepted:
        if business.privacy_policy_url is None:
            await message.answer(
                f"Добро пожаловать в <b>{escape(business.display_name)}</b>! 💅\n\n"
                "Онлайн-запись временно недоступна: владелец ещё не опубликовал политику "
                "конфиденциальности. Команда /whoami продолжает работать."
            )
            return
        await message.answer(
            privacy_text(business.display_name),
            reply_markup=privacy_consent_keyboard(business.privacy_policy_url),
        )
        return
    await _record_pending_acquisition(
        state,
        acquisition_service,
        actor,
    )
    if not status.marketing_answered:
        await message.answer(
            MARKETING_CONSENT_TEXT,
            reply_markup=marketing_consent_keyboard(),
        )
        return
    await message.answer(
        f"С возвращением в <b>{escape(business.display_name)}</b>!",
        reply_markup=client_main_keyboard(await menu_service.get_capabilities()),
    )
    if start_payload is not None and start_payload.startswith("portfolio_"):
        try:
            await feature_flag_service.require_enabled(FeatureName.PORTFOLIO)
        except DomainError:
            return
        try:
            item_id = int(start_payload.removeprefix("portfolio_"))
        except ValueError:
            return
        await show_deep_linked_portfolio_item(message, portfolio_service, bot, item_id)


@router.callback_query(ConsentCallback.filter(F.action == "privacy_accept"))
async def accept_privacy(
    callback: CallbackQuery,
    state: FSMContext,
    consent_service: ConsentService,
    acquisition_service: AcquisitionRuntimeService,
    correlation_id: str,
) -> None:
    await consent_service.accept_privacy(
        actor_from_telegram(callback.from_user),
        correlation_id=correlation_id,
    )
    await _record_pending_acquisition(
        state,
        acquisition_service,
        actor_from_telegram(callback.from_user),
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Согласие на обработку данных сохранено.")
        await callback.message.answer(
            MARKETING_CONSENT_TEXT,
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
    menu_service: MenuService,
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
            reply_markup=client_main_keyboard(await menu_service.get_capabilities()),
        )
    await callback.answer()


@router.message(Command("delete_my_data"))
async def request_data_deletion(
    message: Message,
    presentation_service: PresentationService,
) -> None:
    try:
        business = await presentation_service.get_business()
        recipient = f"в <b>{escape(business.display_name)}</b>"
    except DomainError:
        recipient = "в компанию"
    await message.answer(
        f"Отправить {recipient} запрос на удаление или допустимую анонимизацию ваших данных? "
        "История записей может сохраняться в обезличенном виде в пределах обязательных сроков.",
        reply_markup=deletion_request_keyboard(),
    )


@router.callback_query(ConsentCallback.filter(F.action == "deletion_confirm"))
async def confirm_data_deletion_request(
    callback: CallbackQuery,
    consent_service: ConsentService,
    deletion_request_notification_service: DeletionRequestNotificationService,
    bot: Bot,
    correlation_id: str,
) -> None:
    outcome = await consent_service.request_deletion(
        actor_from_telegram(callback.from_user),
        correlation_id=correlation_id,
    )
    if outcome.created:
        await deletion_request_notification_service.notify(
            bot,
            business_id=outcome.request.business_id,
            request_id=outcome.request.id,
        )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Запрос зарегистрирован. Рекламная подписка отключена. "
            "История записей и финансовые документы могут храниться в обезличенном виде "
            "в пределах обязательных сроков."
        )
    await callback.answer("Запрос зарегистрирован.")


@router.callback_query(ConsentCallback.filter(F.action == "deletion_cancel"))
async def cancel_data_deletion_request(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Запрос на удаление данных отменён.")
    await callback.answer()


def _campaign_code(payload: str | None) -> str | None:
    if payload is None or payload.startswith("portfolio_"):
        return None
    candidate = payload.removeprefix("source_")
    try:
        return validate_campaign_code(candidate)
    except CampaignValidationError:
        return None


async def _accept_staff_invitation(
    message: Message,
    service: AuthorizationService,
    token: str,
    *,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    identity = StaffIdentity(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    try:
        accepted = await service.accept_invitation(
            token,
            identity,
            correlation_id=correlation_id,
        )
    except DomainError:
        await message.answer("Приглашение недействительно, уже использовано или истекло.")
        return
    command = "/master" if accepted.staff.role.value == "master" else "/admin"
    await message.answer(
        f"Приглашение принято. Ваша роль: <b>{escape(accepted.staff.role.value)}</b>.\n"
        f"Откройте рабочую панель командой {command}."
    )


async def _record_pending_acquisition(
    state: FSMContext,
    service: AcquisitionRuntimeService,
    actor: ClientActor,
    *,
    correlation_id: str | None = None,
) -> None:
    data = await state.get_data()
    raw_code = data.pop(_PENDING_ACQUISITION_KEY, None)
    await state.set_data(data)
    if isinstance(raw_code, str):
        await service.record_known_touch(
            actor,
            raw_code=raw_code,
            correlation_id=correlation_id,
        )
