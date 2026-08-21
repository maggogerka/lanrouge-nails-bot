"""Public demo Telegram handlers; no production router is registered in this mode."""

from __future__ import annotations

from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.config import Settings
from app.demo.keyboards import (
    DemoCallback,
    appointments_menu,
    client_services,
    main_menu,
    master_menu,
    reset_confirmation,
    schedule_menu,
    services_menu,
    slot_choices,
)
from app.demo.policy import DemoActionBlocked, DemoOperation
from app.demo.service import DemoError, DemoService

router = Router(name="public_demo")

_STATUS_NAMES = {
    "confirmed": "подтверждена",
    "client_confirmed": "клиент подтвердил",
    "cancelled_by_admin": "отменена мастером",
}


def _user_id(event: Message | CallbackQuery) -> int:
    if event.from_user is None:
        raise DemoError("Не удалось определить Telegram-пользователя.")
    return event.from_user.id


def _date(value: datetime, settings: Settings) -> str:
    return value.astimezone(settings.timezone_info).strftime("%d.%m %H:%M")


async def _send(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=reply_markup)
    await callback.answer()


async def _show_main(
    event: Message | CallbackQuery, demo_service: DemoService, settings: Settings
) -> None:
    workspace = await demo_service.ensure_workspace(_user_id(event))
    site_url = str(settings.demo_site_url) if settings.demo_site_url is not None else None
    text = (
        "<b>CRM for services · публичное демо</b>\n\n"
        "Выберите роль и пройдите реальный сценарий. Все данные тестовые, видны только вам "
        "и автоматически удаляются. Платежи, рассылки и внешние действия отключены."
    )
    markup = main_menu(workspace.generation, site_url)
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
    else:
        await _send(event, text, markup)


@router.message(CommandStart())
@router.message(Command("demo"))
async def start_demo(message: Message, demo_service: DemoService, settings: Settings) -> None:
    await _show_main(message, demo_service, settings)


@router.callback_query(DemoCallback.filter())
async def handle_demo_callback(
    callback: CallbackQuery,
    callback_data: DemoCallback,
    demo_service: DemoService,
    settings: Settings,
) -> None:
    user_id = _user_id(callback)
    action = callback_data.action
    generation = callback_data.generation
    object_id = callback_data.object_id
    try:
        if action == "menu":
            await _show_main(callback, demo_service, settings)
            return
        if action == "client":
            services = await demo_service.list_services(user_id)
            await _send(
                callback,
                "<b>Режим клиента · выберите услугу</b>\n\n"
                "Запись появится только в вашей демонстрационной панели мастера.",
                client_services(generation, services),
            )
            return
        if action == "service":
            slots = await demo_service.list_slots(user_id, generation, object_id)
            markup = slot_choices(
                generation,
                slots,
                lambda value: _date(value, settings),
            )
            await _send(
                callback,
                "<b>Свободные окна</b>\n\nНажмите время, чтобы создать тестовую запись.",
                markup,
            )
            return
        if action == "book":
            appointment = await demo_service.book(user_id, generation, object_id)
            await _send(
                callback,
                "<b>Запись создана</b>\n\n"
                f"{escape(appointment.service_name)} · {_date(appointment.start_at, settings)}\n"
                f"Мастер: {escape(appointment.staff_name)}\n\n"
                "Откройте панель мастера — запись уже там. Это тестовые данные.",
                main_menu(
                    generation,
                    str(settings.demo_site_url) if settings.demo_site_url else None,
                ),
            )
            return
        if action == "master":
            await _send(
                callback,
                "<b>Демонстрационная панель мастера</b>\n\n"
                "Здесь можно безопасно просматривать и менять только ваши тестовые данные.",
                master_menu(generation),
            )
            return
        if action == "appointments":
            appointments = await demo_service.list_appointments(user_id)
            lines = ["<b>Записи</b>", ""]
            lines.extend(
                f"#{item.id} · {_date(item.start_at, settings)} · "
                f"{escape(item.client_name)} · {escape(item.service_name)} · "
                f"{_STATUS_NAMES.get(item.status, item.status)}"
                for item in appointments
            )
            await _send(callback, "\n".join(lines), appointments_menu(generation, appointments))
            return
        if action in {"confirm", "cancel"}:
            status = "client_confirmed" if action == "confirm" else "cancelled_by_admin"
            await demo_service.update_appointment(user_id, generation, object_id, status)
            appointments = await demo_service.list_appointments(user_id)
            await _send(
                callback,
                "Статус тестовой записи обновлён. Никаких уведомлений реальным людям "
                "не отправлено.",
                appointments_menu(generation, appointments),
            )
            return
        if action == "clients":
            clients = await demo_service.list_clients(user_id)
            text = "<b>Демо-клиенты</b>\n\n" + "\n".join(
                f"{escape(name)} · записей: {count}" for name, count in clients
            )
            await _send(callback, text, master_menu(generation))
            return
        if action == "services":
            services = await demo_service.list_services(user_id)
            text = "<b>Услуги</b>\n\n" + "\n".join(
                f"{escape(item.name)} · {item.duration_minutes} мин · {item.price:.0f} ₽"
                for item in services
            )
            await _send(callback, text, services_menu(generation))
            return
        if action == "add_service":
            name = await demo_service.add_service(user_id, generation)
            await _send(
                callback,
                f"Тестовая услуга «{escape(name)}» добавлена только в ваше демо.",
                services_menu(generation),
            )
            return
        if action == "schedule":
            slots = await demo_service.list_schedule(user_id)
            text = "<b>Ближайшие свободные окна</b>\n\n" + "\n".join(
                f"{_date(item.start_at, settings)} · {escape(item.staff_name)} · "
                f"{escape(item.service_name)}"
                for item in slots
            )
            await _send(callback, text, schedule_menu(generation))
            return
        if action == "add_window":
            start_at = await demo_service.add_window(user_id, generation)
            await _send(
                callback,
                f"Свободное окно {_date(start_at, settings)} добавлено только в ваше демо.",
                schedule_menu(generation),
            )
            return
        if action == "help":
            await _send(
                callback,
                "<b>Как пройти демо</b>\n\n"
                "1. Откройте режим клиента и создайте запись.\n"
                "2. Вернитесь в панель мастера.\n"
                "3. Посмотрите запись, клиентов, услуги и расписание.\n"
                "4. Измените статус или добавьте тестовое окно.\n\n"
                "Сессия действует около 2 часов; данные удаляются автоматически.",
                main_menu(
                    generation,
                    str(settings.demo_site_url) if settings.demo_site_url else None,
                ),
            )
            return
        if action == "reset_confirm":
            await _send(
                callback,
                "Удалить и заново создать только ваше демонстрационное пространство?",
                reset_confirmation(generation),
            )
            return
        if action == "reset":
            workspace = await demo_service.reset(user_id, generation)
            await _send(
                callback,
                "Ваши тестовые данные сброшены. Данные других пользователей не изменились.",
                main_menu(
                    workspace.generation,
                    str(settings.demo_site_url) if settings.demo_site_url else None,
                ),
            )
            return
        if action == "payment_demo":
            demo_service.policy.require(DemoOperation.PAYMENT)
        if action == "broadcast_demo":
            demo_service.policy.require(DemoOperation.BROADCAST)
        if action == "order":
            await _send(
                callback,
                "Ссылка на сайт настраивается через DEMO_SITE_URL.",
                main_menu(generation),
            )
            return
        raise DemoError("Неизвестная кнопка. Откройте главное меню заново.")
    except DemoActionBlocked as exc:
        await _send(callback, str(exc), master_menu(generation))
    except DemoError as exc:
        await _send(callback, escape(str(exc)), main_menu(generation))


@router.message(F.text.len() > 300)
async def reject_long_text(message: Message) -> None:
    await message.answer("В демо текст ограничен 300 символами.")


@router.message()
async def recover_navigation(
    message: Message, demo_service: DemoService, settings: Settings
) -> None:
    await _show_main(message, demo_service, settings)
