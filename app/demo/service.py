"""Immutable catalogue and simulated workflows for the public demo.

There are deliberately no database or repository imports in this module. All
displayed entities are fictional value objects. Telegram FSM stores only the
current navigation step for a short, bounded period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo

from app.demo.policy import DemoOperation, DemoPolicy


class DemoScreen(IntEnum):
    SERVICES = 1
    MASTERS = 2
    CLIENT_APPOINTMENTS = 3
    CLIENT_PAYMENTS = 4
    PORTFOLIO = 5
    REVIEWS = 6
    WAITLIST = 7
    NOTIFICATIONS = 8
    CLIENT_SUPPORT = 9
    LEGAL = 10
    TODAY = 20
    UPCOMING = 21
    OPEN_WINDOWS = 22
    CLIENTS = 23
    ADMIN_WAITLIST = 24
    ADMIN_REVIEWS = 25
    DELETION_REQUESTS = 26
    ACTIVE_PREPAYMENTS = 27
    PAYMENT_HISTORY = 28
    ADMIN_SERVICES = 29
    WORKSTATIONS = 30
    STAFF = 31
    ADMIN_PORTFOLIO = 32
    BROADCASTS = 33
    STATISTICS = 34
    FEATURES = 35
    BUSINESS_SETTINGS = 36
    BOT_SETTINGS = 37
    VENDOR_SUPPORT = 38
    SUBSCRIPTION = 39
    MASTER_APPOINTMENTS = 50
    MASTER_WINDOWS = 51
    MASTER_PORTFOLIO = 52
    MASTER_PROFILE = 53


class DemoForm(IntEnum):
    BOOKING = 1
    ADD_WINDOW = 2
    ADD_SERVICE = 3
    BROADCAST = 4
    INVITE_STAFF = 5
    BUSINESS_NAME = 6
    BUSINESS_ADDRESS = 7
    SUPPORT_SOURCE = 8
    BOOKING_SETTINGS = 9
    PAYMENT_SETTINGS = 10
    WORKSTATION = 11
    MASTER_PROFILE = 12
    PORTFOLIO_UPLOAD = 13
    WAITLIST_REQUEST = 14
    REVIEW = 15


@dataclass(frozen=True, slots=True)
class DemoEntity:
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class DemoFormStep:
    key: str
    prompt: str
    choices: tuple[str, ...] = ()

    @property
    def accepts_text(self) -> bool:
        return not self.choices


@dataclass(frozen=True, slots=True)
class DemoFormSpec:
    title: str
    steps: tuple[DemoFormStep, ...]
    operation: DemoOperation
    return_screen: DemoScreen | None = None


SERVICES = (
    DemoEntity(
        "Маникюр с покрытием",
        "2 700 ₽ · 1 ч 30 мин\nСнятие, обработка и однотонное покрытие.",
    ),
    DemoEntity(
        "Укрепление ногтей",
        "3 200 ₽ · 2 часа\nМаникюр, укрепление и покрытие.",
    ),
    DemoEntity(
        "Дизайн",
        "Цена договорная · от 30 мин\nСтоимость зависит от выбранной техники.",
    ),
    DemoEntity("Педикюр", "3 500 ₽ · 2 часа\nПолная обработка и покрытие."),
)

MASTERS = (
    DemoEntity(
        "Анна · мастер",
        "Опыт 6 лет · маникюр, укрепление, дизайн\nСвязь: Telegram и WhatsApp.",
    ),
    DemoEntity(
        "Мария · мастер",
        "Опыт 4 года · маникюр и педикюр\nСвязь: Telegram.",
    ),
)

CLIENT_APPOINTMENTS = (
    DemoEntity(
        "Запись #1042 · ожидает визита",
        "Завтра, 13:00 · Маникюр с покрытием\nМастер: Анна · 2 700 ₽ · предоплата 500 ₽ внесена.",
    ),
    DemoEntity(
        "Запись #1018 · завершена",
        "12 августа, 16:30 · Укрепление ногтей\nМастер: Мария · оплачено 3 200 ₽.",
    ),
)

CLIENT_PAYMENTS = (
    DemoEntity(
        "Предоплата #527 · подтверждена",
        "Запись #1042 · 500 ₽\nСпособ: ручная предоплата · остаток 2 200 ₽.",
    ),
    DemoEntity(
        "Оплата #498 · завершена",
        "Запись #1018 · 3 200 ₽\nПредоплата 500 ₽ · доплата 2 700 ₽.",
    ),
)

APPOINTMENTS = (
    DemoEntity(
        "13:00 · Елена · #1042",
        "Маникюр с покрытием · мастер Анна\n"
        "Телефон: +7 *** ***-12-34 · предоплата 500 ₽ подтверждена.",
    ),
    DemoEntity(
        "15:30 · Ольга · #1043",
        "Укрепление ногтей · мастер Мария\n"
        "Телефон: +7 *** ***-56-78 · ожидает подтверждения предоплаты.",
    ),
    DemoEntity(
        "18:00 · Ирина · #1044",
        "Педикюр · мастер Анна\nТелефон: +7 *** ***-90-12 · без комментария.",
    ),
)

WINDOWS = (
    DemoEntity("Сегодня · 17:00–19:00", "Мастер: Анна · доступно для записи."),
    DemoEntity("Завтра · 11:00–15:00", "Мастер: Мария · доступно для записи."),
    DemoEntity("Завтра · 16:00–20:00", "Мастер: Анна · доступно для записи."),
)

CLIENTS = (
    DemoEntity("Елена · клиент #81", "3 визита · 8 100 ₽ · последний визит 12 августа."),
    DemoEntity("Ольга · клиент #82", "1 визит · 3 200 ₽ · есть будущая запись."),
    DemoEntity("Ирина · клиент #83", "Новый клиент · источник: рекомендация."),
)

PAYMENTS = (
    DemoEntity(
        "500 ₽ · Елена · ожидает проверки",
        "Сегодня, 12:41 · запись #1042\nДоступны подтверждение, отклонение и связь с клиентом.",
    ),
    DemoEntity(
        "500 ₽ · Ольга · подтверждена",
        "Вчера, 18:10 · запись #1043\nПодтверждена администратором.",
    ),
)

PORTFOLIO = (
    DemoEntity("Работа 1 из 3 · Анна", "Маникюр с дизайном · теги: нюд, геометрия."),
    DemoEntity("Работа 2 из 3 · Мария", "Укрепление · теги: короткие ногти."),
    DemoEntity("Работа 3 из 3 · Анна", "Педикюр · теги: классика."),
)

REVIEWS = (
    DemoEntity("★★★★★ · Елена", "«Очень аккуратно и вовремя. Спасибо!»"),
    DemoEntity("★★★★★ · Ольга", "«Удобная запись и прекрасный результат.»"),
)

_FORMS = MappingProxyType(
    {
        DemoForm.BOOKING: DemoFormSpec(
            "Демонстрация записи",
            (
                DemoFormStep("service", "Выберите услугу:", tuple(item.title for item in SERVICES)),
                DemoFormStep(
                    "addon",
                    "Добавить дополнение?",
                    ("Без дополнений", "Снятие · 300 ₽", "Дизайн · договорная"),
                ),
                DemoFormStep(
                    "date",
                    "Выберите дату:",
                    ("Завтра", "Через 2 дня", "Через 3 дня", "Через 5 дней"),
                ),
                DemoFormStep(
                    "time",
                    "Выберите свободное окно:",
                    ("11:00 · Анна", "13:00 · Мария", "16:30 · Анна"),
                ),
                DemoFormStep(
                    "name",
                    "Введите тестовое имя клиента (не указывайте реальные данные):",
                ),
                DemoFormStep("phone", "Введите тестовый номер, например +7 900 000-00-00:"),
                DemoFormStep("comment", "Добавьте тестовый комментарий или отправьте «-»:"),
            ),
            DemoOperation.CREATE_APPOINTMENT,
        ),
        DemoForm.ADD_WINDOW: DemoFormSpec(
            "Добавление свободного окна",
            (
                DemoFormStep("master", "Выберите мастера:", tuple(item.title for item in MASTERS)),
                DemoFormStep(
                    "date",
                    "Выберите дату в календаре:",
                    ("Сегодня", "Завтра", "Через 2 дня", "Через 7 дней"),
                ),
                DemoFormStep(
                    "time", "Выберите время начала:", ("09:00", "11:00", "14:00", "17:00")
                ),
                DemoFormStep(
                    "duration",
                    "Выберите продолжительность окна:",
                    ("1 час", "2 часа", "3 часа", "До конца дня"),
                ),
                DemoFormStep("comment", "Введите комментарий мастеру или отправьте «-»:"),
            ),
            DemoOperation.ADD_WINDOW,
            DemoScreen.OPEN_WINDOWS,
        ),
        DemoForm.ADD_SERVICE: DemoFormSpec(
            "Добавление услуги",
            (
                DemoFormStep("name", "Введите название тестовой услуги:"),
                DemoFormStep("price", "Введите цену в рублях; 0 означает договорную цену:"),
                DemoFormStep(
                    "duration",
                    "Выберите длительность:",
                    ("30 минут", "1 час", "1 ч 30 мин", "2 часа"),
                ),
                DemoFormStep("description", "Введите короткое описание или отправьте «-»:"),
            ),
            DemoOperation.ADD_SERVICE,
            DemoScreen.ADMIN_SERVICES,
        ),
        DemoForm.BROADCAST: DemoFormSpec(
            "Создание рассылки",
            (
                DemoFormStep(
                    "audience",
                    "Выберите аудиторию:",
                    (
                        "Все с согласием",
                        "Клиенты без будущей записи",
                        "Постоянные клиенты",
                    ),
                ),
                DemoFormStep("message", "Введите демонстрационный текст рассылки:"),
            ),
            DemoOperation.BROADCAST,
            DemoScreen.BROADCASTS,
        ),
        DemoForm.INVITE_STAFF: DemoFormSpec(
            "Приглашение сотрудника",
            (
                DemoFormStep("role", "Выберите роль:", ("Администратор", "Менеджер", "Мастер")),
                DemoFormStep("contact", "Введите тестовый Telegram ID или username:"),
            ),
            DemoOperation.STAFF_INVITATION,
            DemoScreen.STAFF,
        ),
        DemoForm.BUSINESS_NAME: DemoFormSpec(
            "Название и описание бизнеса",
            (
                DemoFormStep("name", "Введите тестовое название:"),
                DemoFormStep("description", "Введите тестовое описание:"),
            ),
            DemoOperation.CHANGE_SETTINGS,
            DemoScreen.BUSINESS_SETTINGS,
        ),
        DemoForm.BUSINESS_ADDRESS: DemoFormSpec(
            "Адрес бизнеса",
            (
                DemoFormStep("address", "Введите тестовый адрес:"),
                DemoFormStep("map", "Введите HTTPS-ссылку на карты:"),
            ),
            DemoOperation.CHANGE_SETTINGS,
            DemoScreen.BUSINESS_SETTINGS,
        ),
        DemoForm.SUPPORT_SOURCE: DemoFormSpec(
            "Источник поддержки клиентов",
            (
                DemoFormStep("name", "Введите название источника, например Telegram:"),
                DemoFormStep("url", "Введите HTTPS-ссылку:"),
            ),
            DemoOperation.CHANGE_SETTINGS,
            DemoScreen.BUSINESS_SETTINGS,
        ),
        DemoForm.BOOKING_SETTINGS: DemoFormSpec(
            "Правила записи",
            (
                DemoFormStep(
                    "cancel",
                    "Дедлайн отмены:",
                    ("6 часов", "12 часов", "24 часа", "48 часов"),
                ),
                DemoFormStep(
                    "reschedule",
                    "Дедлайн переноса:",
                    ("6 часов", "12 часов", "24 часа", "48 часов"),
                ),
                DemoFormStep(
                    "reminders",
                    "Напоминания клиенту:",
                    (
                        "За 24 часа",
                        "За 24 и 2 часа",
                        "За 48, 24 и 2 часа",
                    ),
                ),
            ),
            DemoOperation.CHANGE_SETTINGS,
            DemoScreen.BOT_SETTINGS,
        ),
        DemoForm.PAYMENT_SETTINGS: DemoFormSpec(
            "Настройки предоплаты",
            (
                DemoFormStep("mode", "Выберите режим:", ("Без предоплаты", "Ручная", "ЮKassa")),
                DemoFormStep("amount", "Размер предоплаты:", ("500 ₽", "1 000 ₽", "20%", "30%")),
                DemoFormStep(
                    "deadline",
                    "Срок резерва:",
                    ("15 минут", "30 минут", "1 час", "2 часа"),
                ),
            ),
            DemoOperation.CHANGE_PAYMENT,
            DemoScreen.ACTIVE_PREPAYMENTS,
        ),
        DemoForm.WORKSTATION: DemoFormSpec(
            "Добавление рабочего места",
            (
                DemoFormStep("name", "Введите название, например «Маникюрный стол»:"),
                DemoFormStep(
                    "services",
                    "Выберите доступные услуги:",
                    ("Маникюр с покрытием", "Маникюр и укрепление", "Все услуги"),
                ),
            ),
            DemoOperation.CHANGE_SETTINGS,
            DemoScreen.WORKSTATIONS,
        ),
        DemoForm.MASTER_PROFILE: DemoFormSpec(
            "Профиль мастера",
            (
                DemoFormStep("bio", "Введите тестовое описание мастера:"),
                DemoFormStep("link", "Введите тестовую ссылку на социальную сеть:"),
            ),
            DemoOperation.CHANGE_SETTINGS,
            DemoScreen.STAFF,
        ),
        DemoForm.PORTFOLIO_UPLOAD: DemoFormSpec(
            "Добавление работы в портфолио",
            (
                DemoFormStep("description", "Введите тестовое описание работы:"),
                DemoFormStep("tags", "Введите теги через запятую:"),
            ),
            DemoOperation.FILE_UPLOAD,
            DemoScreen.ADMIN_PORTFOLIO,
        ),
        DemoForm.WAITLIST_REQUEST: DemoFormSpec(
            "Лист ожидания",
            (
                DemoFormStep("service", "Выберите услугу:", tuple(item.title for item in SERVICES)),
                DemoFormStep(
                    "date",
                    "Выберите желаемый период:",
                    ("Ближайшие 3 дня", "Эта неделя", "Следующая неделя"),
                ),
            ),
            DemoOperation.CREATE_WAITLIST_ENTRY,
            DemoScreen.WAITLIST,
        ),
        DemoForm.REVIEW: DemoFormSpec(
            "Новый отзыв",
            (
                DemoFormStep("rating", "Выберите оценку:", ("★★★★★", "★★★★", "★★★")),
                DemoFormStep("text", "Введите текст демонстрационного отзыва:"),
            ),
            DemoOperation.CREATE_REVIEW,
            DemoScreen.REVIEWS,
        ),
    }
)


class DemoService:
    """Read-only facade used by handlers; it cannot persist business state."""

    def __init__(self, timezone: ZoneInfo, policy: DemoPolicy | None = None) -> None:
        self.timezone = timezone
        self.policy = policy or DemoPolicy()

    def form(self, form_id: int) -> DemoFormSpec:
        self.policy.require(DemoOperation.READ)
        try:
            return _FORMS[DemoForm(form_id)]
        except (ValueError, KeyError) as exc:
            raise ValueError("unknown demo form") from exc

    def entities(self, screen_id: int) -> tuple[DemoEntity, ...]:
        self.policy.require(DemoOperation.READ)
        screen = DemoScreen(screen_id)
        mappings = {
            DemoScreen.SERVICES: SERVICES,
            DemoScreen.MASTERS: MASTERS,
            DemoScreen.CLIENT_APPOINTMENTS: CLIENT_APPOINTMENTS,
            DemoScreen.CLIENT_PAYMENTS: CLIENT_PAYMENTS,
            DemoScreen.PORTFOLIO: PORTFOLIO,
            DemoScreen.REVIEWS: REVIEWS,
            DemoScreen.TODAY: APPOINTMENTS,
            DemoScreen.UPCOMING: APPOINTMENTS,
            DemoScreen.OPEN_WINDOWS: WINDOWS,
            DemoScreen.CLIENTS: CLIENTS,
            DemoScreen.ACTIVE_PREPAYMENTS: PAYMENTS,
            DemoScreen.PAYMENT_HISTORY: CLIENT_PAYMENTS,
            DemoScreen.ADMIN_SERVICES: SERVICES,
            DemoScreen.ADMIN_PORTFOLIO: PORTFOLIO,
            DemoScreen.MASTER_APPOINTMENTS: APPOINTMENTS,
            DemoScreen.MASTER_WINDOWS: WINDOWS,
            DemoScreen.MASTER_PORTFOLIO: PORTFOLIO,
        }
        return mappings.get(screen, ())

    def relative_dates(self, *, now: datetime | None = None) -> tuple[str, ...]:
        current = (now or datetime.now(self.timezone)).astimezone(self.timezone)
        return tuple(
            (current + timedelta(days=offset)).strftime("%d.%m.%Y") for offset in (1, 2, 3, 5)
        )

    def reject(self, operation: DemoOperation) -> None:
        self.policy.require(operation)
