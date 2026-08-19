"""Client-managed marketing notification preferences."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.enums import ConsentSource
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.main import CLIENT_NOTIFICATIONS_TEXT
from app.keyboards.client.notifications import (
    NotificationSettingsCallback,
    notification_settings_keyboard,
)
from app.schemas.booking import NotificationPreferences
from app.services.consent_service import ConsentService

router = Router(name="client.notifications")


def _text(preferences: NotificationPreferences) -> str:
    marketing = "включены" if preferences.marketing_enabled else "отключены"
    return (
        "<b>Настройки уведомлений</b>\n\n"
        "✅ Сервисные сообщения о записи: всегда включены\n"
        f"{'✅' if preferences.marketing_enabled else '❌'} Рекламные сообщения: {marketing}\n\n"
        "Отказ от рекламы не отключает подтверждения, переносы, оплату и напоминания "
        "по действующей записи."
    )


async def _render(message: Message, preferences: NotificationPreferences, *, edit: bool) -> None:
    markup = notification_settings_keyboard(preferences)
    if edit:
        await message.edit_text(_text(preferences), reply_markup=markup)
    else:
        await message.answer(_text(preferences), reply_markup=markup)


@router.message(F.text == CLIENT_NOTIFICATIONS_TEXT)
async def show_notification_settings(message: Message, consent_service: ConsentService) -> None:
    if message.from_user is None:
        return
    preferences = await consent_service.get_notification_preferences(
        actor_from_telegram(message.from_user)
    )
    await _render(message, preferences, edit=False)


@router.callback_query(NotificationSettingsCallback.filter())
async def change_notification_settings(
    callback: CallbackQuery,
    callback_data: NotificationSettingsCallback,
    consent_service: ConsentService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    if callback_data.action.startswith("marketing_"):
        preferences_status = await consent_service.set_marketing(
            actor,
            accepted=callback_data.action == "marketing_on",
            source=ConsentSource.NOTIFICATION_SETTINGS,
            correlation_id=correlation_id,
        )
        preferences = NotificationPreferences(
            marketing_enabled=preferences_status.marketing_accepted,
            repeat_booking_enabled=(
                await consent_service.get_notification_preferences(actor)
            ).repeat_booking_enabled,
        )
    else:
        await callback.answer(
            "Напоминания о повторной записи больше не используются.", show_alert=True
        )
        return
    if isinstance(callback.message, Message):
        await _render(callback.message, preferences, edit=True)
    await callback.answer("Настройки сохранены")
