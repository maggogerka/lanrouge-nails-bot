"""Owner-facing controls for typed business feature flags."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.features import FeatureName, FeatureSnapshot


class FeatureAdminCallback(CallbackData, prefix="feature"):
    name: FeatureName
    enabled: bool


FEATURE_LABELS: dict[FeatureName, str] = {
    FeatureName.ONLINE_BOOKING: "Онлайн-запись",
    FeatureName.MASTER_SELECTION: "Выбор мастера",
    FeatureName.WAITLIST: "Лист ожидания",
    FeatureName.PORTFOLIO: "Портфолио",
    FeatureName.REVIEWS: "Отзывы",
    FeatureName.REFERENCE_PHOTOS: "Фото-референсы",
    FeatureName.REMINDERS: "Напоминания",
    FeatureName.REPEAT_BOOKING: "Повторная запись",
    FeatureName.BROADCASTS: "Маркетинговые рассылки",
    FeatureName.LOYALTY: "Лояльность",
    FeatureName.STATISTICS: "Статистика",
    FeatureName.PREPAYMENT: "Предоплата",
    FeatureName.MANUAL_PAYMENTS: "Ручная оплата",
    FeatureName.YOOKASSA_PAYMENTS: "YooKassa",
    FeatureName.MINI_APP: "Mini App",
    FeatureName.CLIENT_SUPPORT: "Поддержка клиентов",
}

_HIDDEN_UNFINISHED_FEATURES = {
    FeatureName.LOYALTY,
    FeatureName.MINI_APP,
    FeatureName.REPEAT_BOOKING,
}


def feature_flags_keyboard(
    snapshot: FeatureSnapshot,
    *,
    can_manage: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for feature in FeatureName:
        if feature in _HIDDEN_UNFINISHED_FEATURES:
            continue
        enabled = snapshot.enabled(feature)
        label = f"{'✅' if enabled else '▫️'} {FEATURE_LABELS[feature]}"
        if can_manage:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label,
                        callback_data=FeatureAdminCallback(
                            name=feature,
                            enabled=not enabled,
                        ).pack(),
                    )
                ]
            )
        else:
            rows.append([InlineKeyboardButton(text=label, callback_data="feature_readonly")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
