"""White-label business profile controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import BusinessType


class BusinessProfileCallback(CallbackData, prefix="biz"):
    action: str


class BusinessWelcomeCallback(CallbackData, prefix="bwel"):
    action: str


def business_profile_keyboard(
    *,
    business_type: BusinessType | None = None,
    is_bootstrap_owner: bool = False,
    is_bookable: bool = False,
) -> InlineKeyboardMarkup:
    actions: list[tuple[tuple[str, str], ...]] = [
        (("👋 Приветствие", "welcome"),),
        (("Название", "name"), ("Описание", "description")),
        (("Короткое описание", "short"), ("Тип solo/salon", "type")),
        (("Телефон", "phone"), ("Адрес", "address")),
        (("Часовой пояс", "timezone"), ("Логотип", "logo")),
        (("Политика", "privacy"), ("Оферта", "terms")),
        (("Поддержка клиента", "support_name"), ("Ссылка поддержки", "support_url")),
        (("💼 CRM-подписка", "subscription"),),
        (("🔄 Синхронизировать профиль бота", "sync_bot"),),
    ]
    if is_bootstrap_owner and business_type is BusinessType.SOLO:
        actions.insert(
            3,
            (
                (
                    "Не быть специалистом" if is_bookable else "Работать как специалист",
                    "self_master",
                ),
            ),
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=BusinessProfileCallback(action=action).pack(),
                )
                for label, action in row
            ]
            for row in actions
        ]
    )


def business_welcome_keyboard(*, has_photo: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Изменить текст",
                callback_data=BusinessWelcomeCallback(action="edit_text").pack(),
            ),
            InlineKeyboardButton(
                text="Загрузить фото",
                callback_data=BusinessWelcomeCallback(action="edit_photo").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="Предпросмотр",
                callback_data=BusinessWelcomeCallback(action="preview").pack(),
            ),
            InlineKeyboardButton(
                text="Опубликовать",
                callback_data=BusinessWelcomeCallback(action="publish").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="Стандартное сообщение",
                callback_data=BusinessWelcomeCallback(action="reset").pack(),
            )
        ],
    ]
    if has_photo:
        rows.insert(
            1,
            [
                InlineKeyboardButton(
                    text="Удалить фото из черновика",
                    callback_data=BusinessWelcomeCallback(action="remove_photo").pack(),
                )
            ],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
