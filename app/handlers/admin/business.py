"""Owner-only white-label business setup and Bot API profile synchronization."""

from __future__ import annotations

from html import escape
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InputProfilePhotoStatic,
    Message,
)
from pydantic import ValidationError

from app.domain.enums import BusinessType, SubscriptionStatus
from app.domain.errors import DomainError, EntityNotFoundError
from app.keyboards.admin.business import BusinessProfileCallback, business_profile_keyboard
from app.keyboards.admin.main import ADMIN_BUSINESS_SETTINGS_TEXT
from app.keyboards.admin.services import cancel_keyboard
from app.schemas.authorization import StaffContext
from app.schemas.business import BusinessAdminView, BusinessProfileUpdate
from app.services.business_service import BusinessAdministrationService
from app.services.subscription_service import SubscriptionService
from app.states.business import BusinessProfileStates

router = Router(name="admin.business")

_FIELD_BY_ACTION = {
    "name": "display_name",
    "description": "description",
    "short": "short_description",
    "phone": "contact_phone",
    "address": "address",
    "timezone": "timezone",
    "privacy": "privacy_policy_url",
    "terms": "terms_url",
    "support_name": "client_support_name",
    "support_url": "client_support_url",
}

_PROMPT_BY_ACTION = {
    "name": "Введите название бренда:",
    "description": "Введите описание до 512 символов или «-», чтобы очистить:",
    "short": "Введите короткое описание до 120 символов или «-», чтобы очистить:",
    "phone": "Введите клиентский телефон или «-», чтобы очистить:",
    "address": "Введите адрес или «-», чтобы очистить:",
    "timezone": "Введите IANA timezone, например Europe/Moscow:",
    "privacy": "Введите HTTPS-ссылку на политику:",
    "terms": "Введите HTTPS-ссылку на оферту или «-», чтобы очистить:",
    "support_name": "Введите имя клиентской поддержки или «-», чтобы очистить:",
    "support_url": "Введите HTTPS-ссылку клиентской поддержки или «-», чтобы очистить:",
}


def _render(view: BusinessAdminView) -> str:
    completed = "да" if view.setup_completed_at is not None else "нет"
    return (
        f"<b>Настройки бизнеса</b>\n"
        f"Бренд: <b>{escape(view.display_name)}</b>\n"
        f"Тип: <code>{view.business_type.value}</code> · timezone: <code>{view.timezone}</code>\n"
        f"Телефон: {escape(view.contact_phone or 'не задан')}\n"
        f"Адрес: {escape(view.address or 'не задан')}\n"
        f"Политика: {escape(view.privacy_policy_url or 'не задана')}\n"
        f"Оферта: {escape(view.terms_url or 'не задана')}\n"
        f"Клиентская поддержка: {escape(view.client_support_name or 'не задана')}\n"
        f"Первичная настройка завершена: {completed}"
    )


@router.message(F.text == ADMIN_BUSINESS_SETTINGS_TEXT)
async def show_business_profile(
    message: Message,
    business_service: BusinessAdministrationService,
    subscription_service: SubscriptionService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get(staff_context)
    subscription_text = await _subscription_summary(subscription_service, staff_context.business_id)
    await message.answer(
        _render(view) + f"\n\n{subscription_text}",
        reply_markup=business_profile_keyboard(
            business_type=view.business_type,
            is_bootstrap_owner=staff_context.is_bootstrap_owner,
            is_bookable=staff_context.is_bookable,
        ),
    )


@router.callback_query(BusinessProfileCallback.filter(F.action == "subscription"))
async def show_subscription(
    callback: CallbackQuery,
    subscription_service: SubscriptionService,
    staff_context: StaffContext,
) -> None:
    text = await _subscription_summary(subscription_service, staff_context.business_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(BusinessProfileCallback.filter(F.action == "type"))
async def toggle_business_type(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    current = await business_service.get(staff_context)
    target = BusinessType.SALON if current.business_type is BusinessType.SOLO else BusinessType.SOLO
    try:
        view = await business_service.update(
            staff_context,
            BusinessProfileUpdate(business_type=target),
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render(view),
            reply_markup=business_profile_keyboard(
                business_type=view.business_type,
                is_bootstrap_owner=staff_context.is_bootstrap_owner,
                is_bookable=staff_context.is_bookable,
            ),
        )
    await callback.answer("Тип бизнеса обновлён.")


@router.callback_query(BusinessProfileCallback.filter(F.action == "self_master"))
async def toggle_bootstrap_specialist(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        updated = await business_service.set_bootstrap_bookable(
            staff_context,
            enabled=not staff_context.is_bookable,
            correlation_id=correlation_id,
        )
        view = await business_service.get(updated)
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render(view),
            reply_markup=business_profile_keyboard(
                business_type=view.business_type,
                is_bootstrap_owner=True,
                is_bookable=updated.is_bookable,
            ),
        )
    await callback.answer("Профиль специалиста обновлён.")


@router.callback_query(BusinessProfileCallback.filter(F.action == "logo"))
async def begin_logo_upload(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BusinessProfileStates.waiting_logo)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отправьте квадратное изображение логотипа.",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.callback_query(BusinessProfileCallback.filter(F.action == "sync_bot"))
async def sync_bot_profile(
    callback: CallbackQuery,
    bot: Bot,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get(staff_context)
    try:
        await bot.set_my_name(name=view.display_name[:64])
        await bot.set_my_description(description=(view.description or "")[:512])
        await bot.set_my_short_description(short_description=(view.short_description or "")[:120])
        await bot.set_my_commands(
            commands=[
                BotCommand(command="start", description="Открыть главное меню"),
                BotCommand(command="whoami", description="Показать мой Telegram ID"),
                BotCommand(command="delete_my_data", description="Запросить удаление данных"),
            ]
        )
    except TelegramAPIError:
        await callback.answer("Telegram не применил профиль. Повторите позже.", show_alert=True)
        return
    await callback.answer("Профиль бота синхронизирован.", show_alert=True)


@router.callback_query(BusinessProfileCallback.filter(F.action.in_(set(_FIELD_BY_ACTION))))
async def begin_text_edit(
    callback: CallbackQuery,
    callback_data: BusinessProfileCallback,
    state: FSMContext,
) -> None:
    await state.set_state(BusinessProfileStates.waiting_value)
    await state.set_data({"business_action": callback_data.action})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _PROMPT_BY_ACTION[callback_data.action],
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(BusinessProfileStates.waiting_value)
async def save_text_value(
    message: Message,
    state: FSMContext,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    action = str((await state.get_data()).get("business_action", ""))
    field = _FIELD_BY_ACTION.get(action)
    if field is None:
        await state.clear()
        return
    raw = (message.text or "").strip()
    value = None if raw == "-" else raw
    try:
        update = BusinessProfileUpdate.model_validate({field: value})
        view = await business_service.update(
            staff_context,
            update,
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, ValueError) as exc:
        await message.answer(f"Не удалось сохранить: {escape(str(exc))}")
        return
    await state.clear()
    await message.answer(
        _render(view),
        reply_markup=business_profile_keyboard(
            business_type=view.business_type,
            is_bootstrap_owner=staff_context.is_bootstrap_owner,
            is_bookable=staff_context.is_bookable,
        ),
    )


@router.message(BusinessProfileStates.waiting_logo, F.photo)
async def save_logo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    if not message.photo:
        return
    photo = message.photo[-1]
    view = await business_service.update(
        staff_context,
        BusinessProfileUpdate(logo_telegram_file_id=photo.file_id),
        correlation_id=correlation_id,
    )
    warning = ""
    try:
        buffer = BytesIO()
        await bot.download(photo, destination=buffer)
        uploaded = BufferedInputFile(buffer.getvalue(), filename="business-profile.jpg")
        await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=uploaded))
    except (OSError, TelegramAPIError):
        warning = "\nЛоготип сохранён для меню, но Telegram-профиль не обновился."
    await state.clear()
    await message.answer(
        _render(view) + warning,
        reply_markup=business_profile_keyboard(
            business_type=view.business_type,
            is_bootstrap_owner=staff_context.is_bootstrap_owner,
            is_bookable=staff_context.is_bookable,
        ),
    )


@router.message(BusinessProfileStates.waiting_logo)
async def reject_non_photo(message: Message) -> None:
    await message.answer("Нужно отправить изображение.")


async def _subscription_summary(
    subscription_service: SubscriptionService,
    business_id: int,
) -> str:
    try:
        subscription = await subscription_service.get_status(business_id)
    except EntityNotFoundError:
        return "<b>CRM-подписка</b>\nСтатус ещё не инициализирован. Обратитесь в поддержку."
    status_labels = {
        SubscriptionStatus.TRIAL: "пробный период",
        SubscriptionStatus.ACTIVE: "активна",
        SubscriptionStatus.PAST_DUE: "ожидает оплаты",
        SubscriptionStatus.SUSPENDED: "приостановлена",
        SubscriptionStatus.CANCELLED: "отменена",
    }
    rows = [
        "<b>CRM-подписка</b>",
        f"Тариф: <code>{escape(subscription.plan_code)}</code>",
        f"Статус: {status_labels[subscription.status]}",
    ]
    if subscription.paid_until is not None:
        rows.append(f"Оплачено до: {subscription.paid_until:%d.%m.%Y}")
    if subscription.grace_ends_at is not None:
        rows.append(f"Льготный период до: {subscription.grace_ends_at:%d.%m.%Y}")
    if subscription.next_payment_at is not None:
        rows.append(f"Следующее списание: {subscription.next_payment_at:%d.%m.%Y}")
    if SubscriptionService.owner_warning_due(subscription):
        rows.append("⚠️ Срок подписки заканчивается или требуется оплата.")
    rows.append("Оплата услуг клиентками учитывается отдельно от CRM-подписки.")
    return "\n".join(rows)
