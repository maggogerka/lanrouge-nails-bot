"""Bounded inline navigation for the read-only public demo."""

from __future__ import annotations

from collections.abc import Iterable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.demo.service import DemoForm, DemoScreen


class DemoCallback(CallbackData, prefix="demo"):
    action: str
    target: int = 0
    value: int = 0


def button(text: str, action: str, target: int = 0, value: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=DemoCallback(action=action, target=target, value=value).pack(),
    )


def keyboard(rows: Iterable[Iterable[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


def main_menu(site_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [button("👤 Клиентское меню", "client")],
        [button("🛠 Админ-панель", "admin")],
        [button("💅 Панель мастера", "master")],
        [button("ℹ️ Как работает демо", "help")],
    ]
    if site_url:
        rows.append([InlineKeyboardButton(text="🚀 Получить рабочую версию", url=site_url)])
    else:
        rows.append([button("🚀 Получить рабочую версию", "order")])
    return keyboard(rows)


def client_menu() -> InlineKeyboardMarkup:
    return keyboard(
        (
            (button("✨ Записаться", "form", DemoForm.BOOKING),),
            (button("🧾 Услуги и цены", "screen", DemoScreen.SERVICES),),
            (button("💅 Мастера", "screen", DemoScreen.MASTERS),),
            (button("📅 Мои записи", "screen", DemoScreen.CLIENT_APPOINTMENTS),),
            (button("💳 Мои оплаты", "screen", DemoScreen.CLIENT_PAYMENTS),),
            (button("🖼 Портфолио", "screen", DemoScreen.PORTFOLIO),),
            (button("⭐ Отзывы", "screen", DemoScreen.REVIEWS),),
            (button("⏳ Лист ожидания", "screen", DemoScreen.WAITLIST),),
            (button("🔔 Уведомления", "screen", DemoScreen.NOTIFICATIONS),),
            (button("☎️ Поддержка и контакты", "screen", DemoScreen.CLIENT_SUPPORT),),
            (button("🔐 Политика и оферта", "screen", DemoScreen.LEGAL),),
            (button("← Выбор режима", "menu"),),
        )
    )


def admin_menu(page: int = 0) -> InlineKeyboardMarkup:
    if page == 0:
        rows = [
            [button("📍 Сегодня", "screen", DemoScreen.TODAY)],
            [button("📅 Ближайшие записи", "screen", DemoScreen.UPCOMING)],
            [button("➕ Добавить окно", "form", DemoForm.ADD_WINDOW)],
            [button("🗓 Открытые окна", "screen", DemoScreen.OPEN_WINDOWS)],
            [button("👥 Клиенты", "screen", DemoScreen.CLIENTS)],
            [button("⏳ Лист ожидания", "screen", DemoScreen.ADMIN_WAITLIST)],
            [button("⭐ Отзывы", "screen", DemoScreen.ADMIN_REVIEWS)],
            [button("🗑 Запросы на удаление", "screen", DemoScreen.DELETION_REQUESTS)],
            [button("💳 Предоплаты", "screen", DemoScreen.ACTIVE_PREPAYMENTS)],
            [button("Далее 1/2 →", "admin", value=1)],
            [button("← Выбор режима", "menu")],
        ]
    else:
        rows = [
            [button("🧾 Услуги", "screen", DemoScreen.ADMIN_SERVICES)],
            [button("🪑 Рабочие места", "screen", DemoScreen.WORKSTATIONS)],
            [button("👩‍💼 Мастера и сотрудники", "screen", DemoScreen.STAFF)],
            [button("🖼 Портфолио", "screen", DemoScreen.ADMIN_PORTFOLIO)],
            [button("📣 Рассылки", "screen", DemoScreen.BROADCASTS)],
            [button("📊 Статистика", "screen", DemoScreen.STATISTICS)],
            [button("🧩 Функции бота", "screen", DemoScreen.FEATURES)],
            [button("🏢 Настройки бизнеса", "screen", DemoScreen.BUSINESS_SETTINGS)],
            [button("⚙️ Настройки", "screen", DemoScreen.BOT_SETTINGS)],
            [button("🛟 Техподдержка CRM", "screen", DemoScreen.VENDOR_SUPPORT)],
            [button("💼 CRM-подписка", "screen", DemoScreen.SUBSCRIPTION)],
            [button("← Назад 2/2", "admin", value=0)],
            [button("← Выбор режима", "menu")],
        ]
    return keyboard(rows)


def master_menu() -> InlineKeyboardMarkup:
    return keyboard(
        (
            (button("📅 Мои записи", "screen", DemoScreen.MASTER_APPOINTMENTS),),
            (button("➕ Открыть окно", "form", DemoForm.ADD_WINDOW),),
            (button("🗓 Мои открытые окна", "screen", DemoScreen.MASTER_WINDOWS),),
            (button("🖼 Моё портфолио", "screen", DemoScreen.MASTER_PORTFOLIO),),
            (button("👤 Мой профиль", "screen", DemoScreen.MASTER_PROFILE),),
            (button("🛟 Техподдержка CRM", "screen", DemoScreen.VENDOR_SUPPORT),),
            (button("← Выбор режима", "menu"),),
        )
    )


def choices(form_id: int, labels: tuple[str, ...]) -> InlineKeyboardMarkup:
    rows = [[button(label, "choice", form_id, index)] for index, label in enumerate(labels)]
    rows.append([button("Отменить оформление", "cancel_form")])
    return keyboard(rows)


def confirmation(form_id: int) -> InlineKeyboardMarkup:
    return keyboard(
        (
            (button("✅ Подтвердить", "finish", form_id),),
            (button("← Изменить данные", "restart_form", form_id),),
            (button("Отменить оформление", "cancel_form"),),
        )
    )


def pager(
    screen: DemoScreen,
    index: int,
    total: int,
    *,
    action_form: DemoForm | None = None,
    action_label: str | None = None,
    blocked_action: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if total > 1:
        rows.append(
            [
                button("←", "screen", screen, (index - 1) % total),
                button(f"{index + 1}/{total}", "noop"),
                button("→", "screen", screen, (index + 1) % total),
            ]
        )
    if action_form is not None and action_label is not None:
        rows.append([button(action_label, "form", action_form)])
    if blocked_action:
        rows.append([button("Управлять", "manage", screen)])
    rows.append([button("← В раздел", "screen_back", screen)])
    return keyboard(rows)


def simple_back(destination: str = "admin") -> InlineKeyboardMarkup:
    return keyboard(((button("← Назад", destination),),))


def screen_actions(screen: DemoScreen) -> InlineKeyboardMarkup:
    actions: dict[DemoScreen, tuple[tuple[str, str, int], ...]] = {
        DemoScreen.WAITLIST: (("➕ Встать в лист ожидания", "form", DemoForm.WAITLIST_REQUEST),),
        DemoScreen.REVIEWS: (("⭐ Оставить отзыв", "form", DemoForm.REVIEW),),
        DemoScreen.ADMIN_WAITLIST: (("Уведомить клиента", "blocked", 0),),
        DemoScreen.ADMIN_REVIEWS: (("Опубликовать / скрыть", "blocked", 0),),
        DemoScreen.DELETION_REQUESTS: (("Обработать запрос", "blocked", 0),),
        DemoScreen.ACTIVE_PREPAYMENTS: (
            ("⚙️ Настроить предоплату", "form", DemoForm.PAYMENT_SETTINGS),
            ("Подтвердить / отклонить", "blocked", 0),
            ("История оплат", "screen", DemoScreen.PAYMENT_HISTORY),
        ),
        DemoScreen.ADMIN_SERVICES: (("➕ Добавить услугу", "form", DemoForm.ADD_SERVICE),),
        DemoScreen.WORKSTATIONS: (("➕ Добавить рабочее место", "form", DemoForm.WORKSTATION),),
        DemoScreen.STAFF: (
            ("➕ Пригласить сотрудника", "form", DemoForm.INVITE_STAFF),
            ("✏️ Настроить профиль мастера", "form", DemoForm.MASTER_PROFILE),
        ),
        DemoScreen.ADMIN_PORTFOLIO: (("➕ Добавить работу", "form", DemoForm.PORTFOLIO_UPLOAD),),
        DemoScreen.BROADCASTS: (("➕ Создать рассылку", "form", DemoForm.BROADCAST),),
        DemoScreen.FEATURES: (
            ("Портфолио: включено", "blocked", 0),
            ("Отзывы: включены", "blocked", 0),
            ("Лист ожидания: включён", "blocked", 0),
            ("Рассылки: включены", "blocked", 0),
        ),
        DemoScreen.BUSINESS_SETTINGS: (
            ("Название и описание", "form", DemoForm.BUSINESS_NAME),
            ("Адрес и карта", "form", DemoForm.BUSINESS_ADDRESS),
            ("Источник поддержки", "form", DemoForm.SUPPORT_SOURCE),
            ("Телефон / логотип / документы", "blocked", 0),
            ("Синхронизировать профиль", "blocked", 0),
        ),
        DemoScreen.BOT_SETTINGS: (
            ("Правила записи и напоминания", "form", DemoForm.BOOKING_SETTINGS),
            ("Часовой пояс", "blocked", 0),
            ("Ограничения записи", "blocked", 0),
        ),
        DemoScreen.MASTER_PROFILE: (("✏️ Изменить профиль", "form", DemoForm.MASTER_PROFILE),),
    }
    rows = [
        [button(label, action, int(target))] for label, action, target in actions.get(screen, ())
    ]
    back = "client" if int(screen) < 20 else "master" if int(screen) >= 50 else "admin"
    rows.append([button("← Назад", back)])
    return keyboard(rows)


def management_actions(screen: DemoScreen) -> InlineKeyboardMarkup:
    """Show realistic final actions before the central write barrier."""

    actions: dict[DemoScreen, tuple[str, ...]] = {
        DemoScreen.CLIENT_APPOINTMENTS: (
            "🔄 Перенести запись",
            "❌ Отменить запись",
            "💬 Написать мастеру",
        ),
        DemoScreen.CLIENT_PAYMENTS: (
            "✅ Я оплатил(а)",
            "📎 Прикрепить чек",
            "💬 Связаться с салоном",
        ),
        DemoScreen.TODAY: (
            "✅ Завершить визит",
            "🚫 Отметить неявку",
            "💬 Написать клиенту",
            "🔄 Перенести запись",
            "❌ Отменить запись",
        ),
        DemoScreen.UPCOMING: (
            "💬 Написать клиенту",
            "🔄 Перенести запись",
            "❌ Отменить запись",
        ),
        DemoScreen.OPEN_WINDOWS: (
            "📦 Архивировать окно",
            "🗑 Удалить окно",
            "⚠️ Удалить принудительно",
        ),
        DemoScreen.CLIENTS: (
            "📖 Подробная история",
            "➕ Создать запись",
            "🏷 Изменить теги",
            "📝 Добавить заметку",
            "💬 Написать клиенту",
        ),
        DemoScreen.ACTIVE_PREPAYMENTS: (
            "✅ Подтвердить оплату",
            "❌ Отклонить оплату",
            "💬 Написать клиенту",
            "↩️ Оформить возврат",
        ),
        DemoScreen.PAYMENT_HISTORY: ("Открыть запись", "↩️ Оформить возврат"),
        DemoScreen.ADMIN_SERVICES: (
            "✏️ Изменить услугу",
            "👩‍💼 Назначить мастеров",
            "📦 Архивировать",
            "⚠️ Удалить принудительно",
        ),
        DemoScreen.ADMIN_PORTFOLIO: (
            "✏️ Изменить описание",
            "📦 Архивировать",
            "🗑 Удалить работу",
        ),
        DemoScreen.MASTER_APPOINTMENTS: (
            "✅ Завершить визит",
            "🚫 Отметить неявку",
            "💬 Написать клиенту",
        ),
        DemoScreen.MASTER_WINDOWS: ("📦 Архивировать окно", "🗑 Удалить окно"),
    }
    rows = [[button(label, "blocked")] for label in actions.get(screen, ("Изменить",))]
    rows.append([button("← К карточке", "screen", screen)])
    return keyboard(rows)
