"""Public-demo handlers with simulated UX and no business persistence."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.config import Settings
from app.demo.keyboards import (
    DemoCallback,
    admin_menu,
    choices,
    client_menu,
    confirmation,
    main_menu,
    management_actions,
    master_menu,
    pager,
    screen_actions,
)
from app.demo.policy import DemoActionBlocked, DemoOperation
from app.demo.service import DemoForm, DemoScreen, DemoService

router = Router(name="public_demo")

MAX_DEMO_INPUT_LENGTH = 300


class DemoFormState(StatesGroup):
    collecting = State()


_SCREEN_COPY: dict[DemoScreen, tuple[str, str]] = {
    DemoScreen.SERVICES: ("Услуги и цены", "Листайте карточки и начните запись с любой услуги."),
    DemoScreen.MASTERS: ("Мастера", "Карточки мастеров, специализация, контакты и запись."),
    DemoScreen.CLIENT_APPOINTMENTS: (
        "Мои записи",
        "Перенос и отмена показаны, но в демо не изменяют данные.",
    ),
    DemoScreen.CLIENT_PAYMENTS: (
        "Мои оплаты",
        "Клиент видит только собственные фиктивные оплаты и инструкции.",
    ),
    DemoScreen.PORTFOLIO: ("Портфолио", "Работы листаются по одной карточке."),
    DemoScreen.REVIEWS: ("Отзывы", "Опубликованные отзывы и сценарий добавления нового."),
    DemoScreen.WAITLIST: ("Лист ожидания", "Заявка создаётся только в рабочей версии."),
    DemoScreen.NOTIFICATIONS: (
        "Уведомления",
        "Напоминания о записи: за 24 часа и за 2 часа. Рассылки требуют отдельного согласия.",
    ),
    DemoScreen.CLIENT_SUPPORT: (
        "Поддержка и контакты",
        "Телефон салона: +7 900 000-00-00\nTelegram: @example_support\n"
        "Адрес: Москва, Тестовая улица, 1",
    ),
    DemoScreen.LEGAL: (
        "Политика и оферта",
        "В рабочем боте владелец добавляет утверждённые HTTPS-ссылки на политику, "
        "оферту и правила отмены.",
    ),
    DemoScreen.TODAY: (
        "Сегодня",
        "Записи открываются карточками со статусом, оплатой и действиями.",
    ),
    DemoScreen.UPCOMING: (
        "Ближайшие записи",
        "Список разбит по страницам; каждая запись управляется отдельно.",
    ),
    DemoScreen.OPEN_WINDOWS: (
        "Открытые окна",
        "Свободные интервалы мастеров. Архив можно скрывать.",
    ),
    DemoScreen.CLIENTS: ("Клиенты", "CRM-карточка, история, заметки, теги и создание записи."),
    DemoScreen.ADMIN_WAITLIST: (
        "Лист ожидания",
        "Администратор видит заявки и может предложить освободившееся окно.",
    ),
    DemoScreen.ADMIN_REVIEWS: ("Отзывы", "Модерация, публикация и скрытие отзывов."),
    DemoScreen.DELETION_REQUESTS: (
        "Запросы на удаление",
        "Обработка заявлений субъекта данных с журналом действий.",
    ),
    DemoScreen.ACTIVE_PREPAYMENTS: (
        "Действующие предоплаты",
        "Проверка, подтверждение, отклонение и связь с клиентом.",
    ),
    DemoScreen.PAYMENT_HISTORY: (
        "История оплат",
        "Завершённые и отклонённые платежи с датой, суммой и записью.",
    ),
    DemoScreen.ADMIN_SERVICES: ("Услуги", "Цена, длительность, дополнения, мастера и архив."),
    DemoScreen.WORKSTATIONS: (
        "Рабочие места",
        "Ограничивают одновременную занятость мастеров для выбранных услуг.",
    ),
    DemoScreen.STAFF: (
        "Мастера и сотрудники",
        "Роли, привязка Telegram, услуги, профиль и публичные ссылки.",
    ),
    DemoScreen.ADMIN_PORTFOLIO: (
        "Портфолио",
        "Владелец управляет работами всех мастеров; мастер — только своими.",
    ),
    DemoScreen.BROADCASTS: (
        "Рассылки",
        "Создание, аудитория, предпросмотр и отправка только клиентам с согласием.",
    ),
    DemoScreen.STATISTICS: (
        "Статистика",
        "Тестовые показатели: 24 записи · 18 завершено · выручка 54 300 ₽ · средний чек 3 017 ₽.",
    ),
    DemoScreen.FEATURES: (
        "Функции бота",
        "Здесь владелец включает клиентские разделы. В демо переключатели неизменяемы.",
    ),
    DemoScreen.BUSINESS_SETTINGS: (
        "Настройки бизнеса",
        "Название, описание, адрес, карта, телефон, логотип, документы и поддержка.",
    ),
    DemoScreen.BOT_SETTINGS: (
        "Настройки",
        "Дедлайны, напоминания, ограничения записи и часовой пояс.",
    ),
    DemoScreen.VENDOR_SUPPORT: (
        "Техническая поддержка CRM",
        "В рабочей установке здесь будет контакт вашей технической поддержки.",
    ),
    DemoScreen.SUBSCRIPTION: (
        "CRM-подписка",
        "Информационный экран. Продление и биллинг не выполняются в публичном демо.",
    ),
    DemoScreen.MASTER_APPOINTMENTS: (
        "Мои записи мастера",
        "Только назначенные этому мастеру записи и переход к карточке визита.",
    ),
    DemoScreen.MASTER_WINDOWS: (
        "Мои открытые окна",
        "Мастер видит и управляет только своим расписанием.",
    ),
    DemoScreen.MASTER_PORTFOLIO: (
        "Моё портфолио",
        "Мастер видит и редактирует только собственные работы.",
    ),
    DemoScreen.MASTER_PROFILE: (
        "Мой профиль",
        "Анна · мастер\nОпыт 6 лет · маникюр, укрепление, дизайн\nTelegram и WhatsApp подключены.",
    ),
}


_PAGER_ACTIONS: dict[DemoScreen, tuple[DemoForm | None, str | None, bool]] = {
    DemoScreen.SERVICES: (DemoForm.BOOKING, "✨ Записаться", False),
    DemoScreen.MASTERS: (DemoForm.BOOKING, "✨ Записаться", False),
    DemoScreen.CLIENT_APPOINTMENTS: (None, None, True),
    DemoScreen.CLIENT_PAYMENTS: (None, None, True),
    DemoScreen.TODAY: (None, None, True),
    DemoScreen.UPCOMING: (None, None, True),
    DemoScreen.OPEN_WINDOWS: (DemoForm.ADD_WINDOW, "➕ Добавить окно", True),
    DemoScreen.CLIENTS: (None, None, True),
    DemoScreen.ACTIVE_PREPAYMENTS: (DemoForm.PAYMENT_SETTINGS, "⚙️ Настроить", True),
    DemoScreen.PAYMENT_HISTORY: (None, None, True),
    DemoScreen.ADMIN_SERVICES: (DemoForm.ADD_SERVICE, "➕ Добавить услугу", True),
    DemoScreen.ADMIN_PORTFOLIO: (DemoForm.PORTFOLIO_UPLOAD, "➕ Добавить работу", True),
    DemoScreen.MASTER_APPOINTMENTS: (None, None, True),
    DemoScreen.MASTER_WINDOWS: (DemoForm.ADD_WINDOW, "➕ Открыть окно", True),
}


def _site_url(settings: Settings) -> str | None:
    return str(settings.demo_site_url) if settings.demo_site_url is not None else None


async def _replace(
    event: Message | CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
        return
    await event.answer()
    if not isinstance(event.message, Message):
        return
    try:
        await event.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await event.message.answer(text, reply_markup=markup)


async def _show_main(event: Message | CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await _replace(
        event,
        "<b>Публичная демонстрация Telegram CRM</b>\n\n"
        "Можно открыть клиентское меню, админ-панель и панель мастера без регистрации. "
        "Все примеры вымышлены. Демо не создаёт записи, не меняет настройки, не хранит "
        "введённый текст и не отправляет сообщения или платежи.",
        main_menu(_site_url(settings)),
    )


@router.message(CommandStart())
@router.message(Command("demo"))
async def start_demo(message: Message, state: FSMContext, settings: Settings) -> None:
    await _show_main(message, state, settings)


async def _show_screen(
    event: Message | CallbackQuery,
    service: DemoService,
    screen: DemoScreen,
    index: int,
) -> None:
    title, intro = _SCREEN_COPY[screen]
    entities = service.entities(screen)
    if not entities:
        await _replace(event, f"<b>{escape(title)}</b>\n\n{escape(intro)}", screen_actions(screen))
        return
    safe_index = index % len(entities)
    entity = entities[safe_index]
    action_form, action_label, blocked_action = _PAGER_ACTIONS.get(screen, (None, None, False))
    await _replace(
        event,
        f"<b>{escape(title)}</b>\n\n{escape(entity.title)}\n{escape(entity.body)}\n\n"
        f"<i>{escape(intro)}</i>",
        pager(
            screen,
            safe_index,
            len(entities),
            action_form=action_form,
            action_label=action_label,
            blocked_action=blocked_action,
        ),
    )


async def _show_form_step(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: DemoService,
) -> None:
    data = await state.get_data()
    form_id = int(data["form_id"])
    step_index = int(data.get("step", 0))
    spec = service.form(form_id)
    if step_index >= len(spec.steps):
        answers = data.get("answers", [])
        summary = "\n".join(f"• {escape(str(item))}" for item in answers)
        await _replace(
            event,
            f"<b>{escape(spec.title)} · проверка</b>\n\n{summary}\n\n"
            "Нажмите «Подтвердить», чтобы увидеть финальный этап. Данные не сохраняются.",
            confirmation(form_id),
        )
        return
    step = spec.steps[step_index]
    prompt = step.prompt
    labels = step.choices
    if step.key == "date" and labels:
        dates = service.relative_dates()
        labels = tuple(
            f"{label} · {dates[min(position, len(dates) - 1)]}"
            for position, label in enumerate(labels)
        )
    markup = choices(form_id, labels) if labels else None
    suffix = (
        "\n\nТекст используется только для перехода к следующему экрану и не сохраняется."
        if step.accepts_text
        else ""
    )
    await _replace(
        event,
        f"<b>{escape(spec.title)}</b>\n\nШаг {step_index + 1} из {len(spec.steps)}\n"
        f"{escape(prompt)}{suffix}",
        markup,
    )


async def _start_form(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: DemoService,
    form_id: int,
) -> None:
    service.policy.require(DemoOperation.TRANSIENT_STATE)
    service.form(form_id)
    await state.set_state(DemoFormState.collecting)
    await state.set_data({"form_id": form_id, "step": 0, "answers": []})
    await _show_form_step(event, state, service)


async def _accept_answer(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: DemoService,
    answer: str,
) -> None:
    data = await state.get_data()
    form_id = int(data["form_id"])
    spec = service.form(form_id)
    step_index = int(data.get("step", 0))
    if step_index >= len(spec.steps):
        await _show_form_step(event, state, service)
        return
    answers = list(data.get("answers", []))
    answers.append(f"{spec.steps[step_index].prompt} {answer}")
    await state.update_data(step=step_index + 1, answers=answers)
    await _show_form_step(event, state, service)


@router.callback_query(DemoCallback.filter())
async def handle_demo_callback(
    callback: CallbackQuery,
    callback_data: DemoCallback,
    state: FSMContext,
    demo_service: DemoService,
    settings: Settings,
) -> None:
    action = callback_data.action
    try:
        if action == "noop":
            await callback.answer()
        elif action == "menu":
            await _show_main(callback, state, settings)
        elif action == "client":
            await state.clear()
            await _replace(
                callback,
                "<b>Клиентское меню</b>\n\nВсе данные на экранах вымышлены.",
                client_menu(),
            )
        elif action == "admin":
            await state.clear()
            await _replace(
                callback,
                "<b>Демонстрационная админ-панель</b>\n\n"
                "Доступны все основные разделы владельца. Изменения блокируются только "
                "на финальном подтверждении.",
                admin_menu(callback_data.value),
            )
        elif action == "master":
            await state.clear()
            await _replace(
                callback,
                "<b>Демонстрационная панель мастера</b>\n\n"
                "Показана self-scoped работа мастера без доступа к данным коллег.",
                master_menu(),
            )
        elif action == "screen":
            await state.clear()
            await _show_screen(
                callback,
                demo_service,
                DemoScreen(callback_data.target),
                callback_data.value,
            )
        elif action == "screen_back":
            screen = DemoScreen(callback_data.target)
            destination = (
                "client" if int(screen) < 20 else "master" if int(screen) >= 50 else "admin"
            )
            callback_data = DemoCallback(action=destination)
            if destination == "client":
                await _replace(callback, "<b>Клиентское меню</b>", client_menu())
            elif destination == "master":
                await _replace(callback, "<b>Панель мастера</b>", master_menu())
            else:
                await _replace(callback, "<b>Админ-панель</b>", admin_menu())
        elif action == "manage":
            screen = DemoScreen(callback_data.target)
            await _replace(
                callback,
                "<b>Действия с карточкой</b>\n\n"
                "Выберите действие. Оно будет остановлено перед изменением данных.",
                management_actions(screen),
            )
        elif action in {"form", "restart_form"}:
            await _start_form(callback, state, demo_service, callback_data.target)
        elif action == "choice":
            data = await state.get_data()
            if int(data.get("form_id", -1)) != callback_data.target:
                raise ValueError("Форма устарела. Откройте сценарий заново.")
            spec = demo_service.form(callback_data.target)
            step_index = int(data.get("step", 0))
            step = spec.steps[step_index]
            if not 0 <= callback_data.value < len(step.choices):
                raise ValueError("Вариант больше недоступен.")
            await _accept_answer(
                callback,
                state,
                demo_service,
                step.choices[callback_data.value],
            )
        elif action == "finish":
            data = await state.get_data()
            if int(data.get("form_id", -1)) != callback_data.target:
                raise ValueError("Форма устарела. Откройте сценарий заново.")
            spec = demo_service.form(callback_data.target)
            demo_service.reject(spec.operation)
        elif action == "cancel_form":
            await state.clear()
            await _replace(
                callback,
                "Оформление отменено. Никакие данные не были сохранены.",
                client_menu(),
            )
        elif action == "blocked":
            demo_service.reject(DemoOperation.CHANGE_SETTINGS)
        elif action == "help":
            await _replace(
                callback,
                "<b>Как устроено публичное демо</b>\n\n"
                "1. Выберите роль клиента, владельца или мастера.\n"
                "2. Откройте любой раздел и пройдите сценарий до подтверждения.\n"
                "3. На финальном шаге бот объяснит, что сохранение доступно после покупки.\n\n"
                "PostgreSQL к демоботу не подключён. Платежи, рассылки, приглашения, "
                "файлы и уведомления не отправляются. Навигация хранится в Redis "
                "ограниченное время.",
                main_menu(_site_url(settings)),
            )
        elif action == "order":
            await _replace(
                callback,
                "Ссылка на покупку ещё не настроена владельцем демо. Укажите DEMO_SITE_URL.",
                main_menu(_site_url(settings)),
            )
        else:
            raise ValueError("Неизвестная или устаревшая кнопка.")
    except DemoActionBlocked as exc:
        await state.clear()
        await _replace(
            callback,
            f"<b>Демонстрация завершена</b>\n\n{escape(str(exc))}",
            main_menu(_site_url(settings)),
        )
    except (KeyError, ValueError, IndexError):
        await state.clear()
        await _replace(
            callback,
            "Эта кнопка устарела. Откройте нужный раздел заново.",
            main_menu(_site_url(settings)),
        )


@router.message(DemoFormState.collecting)
async def collect_form_text(
    message: Message,
    state: FSMContext,
    demo_service: DemoService,
) -> None:
    data = await state.get_data()
    try:
        spec = demo_service.form(int(data["form_id"]))
        step_index = int(data.get("step", 0))
        step = spec.steps[step_index]
    except (KeyError, ValueError, IndexError):
        await state.clear()
        await message.answer("Форма устарела. Отправьте /start.")
        return
    if not step.accepts_text:
        await message.answer("На этом шаге выберите один из вариантов кнопкой.")
        return
    if message.text is None:
        await message.answer(
            "Файлы, контакты и медиа в публичном демо не принимаются. Введите тестовый текст."
        )
        return
    value = message.text.strip()
    if not value or len(value) > MAX_DEMO_INPUT_LENGTH:
        await message.answer("Введите от 1 до 300 символов.")
        return
    await _accept_answer(
        message,
        state,
        demo_service,
        "<введено; значение не сохраняется>",
    )


@router.message(F.text.len() > MAX_DEMO_INPUT_LENGTH)
async def reject_long_text(message: Message) -> None:
    await message.answer("В публичном демо текст ограничен 300 символами.")


@router.message()
async def recover_navigation(message: Message, state: FSMContext, settings: Settings) -> None:
    await _show_main(message, state, settings)
