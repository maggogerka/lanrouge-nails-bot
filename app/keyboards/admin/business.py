"""White-label business profile controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import BusinessType
from app.schemas.public_links import PublicLink
from app.schemas.workstation import WorkstationView


class BusinessProfileCallback(CallbackData, prefix="biz"):
    action: str


class BusinessWelcomeCallback(CallbackData, prefix="bwel"):
    action: str


class BusinessSupportCallback(CallbackData, prefix="bizsup"):
    action: str
    index: int = -1


class BusinessTimezoneCallback(CallbackData, prefix="biztz"):
    timezone: str


class WorkstationCallback(CallbackData, prefix="wst"):
    action: str
    workstation_id: int = 0
    service_id: int = 0


def business_profile_keyboard(
    *,
    business_type: BusinessType | None = None,
    is_bootstrap_owner: bool = False,
    is_bookable: bool = False,
) -> InlineKeyboardMarkup:
    del business_type, is_bootstrap_owner, is_bookable
    actions: list[tuple[tuple[str, str], ...]] = [
        (("👋 Приветствие", "welcome"),),
        (("🏷 Название", "name"), ("📝 Описание", "description")),
        (("✍️ Короткое описание", "short"), ("🖼 Логотип", "logo")),
        (("☎️ Телефон салона", "phone"), ("📍 Адрес и карта", "address_menu")),
        (("🕐 Часовой пояс", "timezone_menu"),),
        (("🪑 Рабочие места", "workstations"),),
        (("🔒 Политика", "privacy"), ("📄 Оферта", "terms")),
        (("🛟 Источники поддержки", "support_sources"),),
        (("💼 CRM-подписка", "subscription"),),
        (("🔄 Синхронизировать профиль бота", "sync_bot"),),
    ]
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


def business_address_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Изменить адрес",
                    callback_data=BusinessProfileCallback(action="address").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗺 Изменить ссылку на карту",
                    callback_data=BusinessProfileCallback(action="map_url").pack(),
                )
            ],
        ]
    )


def business_support_keyboard(links: tuple[PublicLink, ...]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🗑 {link.label[:45]}",
                callback_data=BusinessSupportCallback(action="delete", index=index).pack(),
            )
        ]
        for index, link in enumerate(links)
    ]
    if len(links) < 5:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить источник",
                    callback_data=BusinessSupportCallback(action="add").pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def workstation_list_keyboard(
    workstations: tuple[WorkstationView, ...],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if item.is_active else '📦'} {item.name[:40]}",
                callback_data=WorkstationCallback(
                    action="view",
                    workstation_id=item.id,
                ).pack(),
            )
        ]
        for item in workstations
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Создать рабочее место",
                callback_data=WorkstationCallback(action="create").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def workstation_details_keyboard(item: WorkstationView) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if service.enabled else "⬜ ") + service.service_name[:38],
                callback_data=WorkstationCallback(
                    action="service_off" if service.enabled else "service_on",
                    workstation_id=item.id,
                    service_id=service.service_id,
                ).pack(),
            )
        ]
        for service in item.services
        if service.service_active
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="📦 Архивировать" if item.is_active else "♻️ Восстановить",
                    callback_data=WorkstationCallback(
                        action="archive" if item.is_active else "restore",
                        workstation_id=item.id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К рабочим местам",
                    callback_data=WorkstationCallback(action="list").pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def business_timezone_keyboard() -> InlineKeyboardMarkup:
    choices = (
        ("Калининград", "Europe/Kaliningrad"),
        ("Москва", "Europe/Moscow"),
        ("Самара", "Europe/Samara"),
        ("Екатеринбург", "Asia/Yekaterinburg"),
        ("Омск", "Asia/Omsk"),
        ("Красноярск", "Asia/Krasnoyarsk"),
        ("Иркутск", "Asia/Irkutsk"),
        ("Якутск", "Asia/Yakutsk"),
        ("Владивосток", "Asia/Vladivostok"),
        ("Магадан", "Asia/Magadan"),
        ("Камчатка", "Asia/Kamchatka"),
    )
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=BusinessTimezoneCallback(timezone=timezone).pack(),
            )
            for label, timezone in choices[index : index + 2]
        ]
        for index in range(0, len(choices), 2)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Другой город / пояс",
                callback_data=BusinessProfileCallback(action="timezone").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
