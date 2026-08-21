"""Compact inline navigation for demo client and management modes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class DemoCallback(CallbackData, prefix="pd"):
    action: str
    generation: int
    object_id: int = 0


def button(text: str, action: str, generation: int, object_id: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=DemoCallback(
            action=action, generation=generation, object_id=object_id
        ).pack(),
    )


def main_menu(generation: int, site_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [button("👤 Попробовать как клиент", "client", generation)],
        [button("🗓 Панель мастера", "master", generation)],
        [button("ℹ️ Как это работает", "help", generation)],
        [button("♻️ Сбросить демо", "reset_confirm", generation)],
    ]
    if site_url:
        rows.append([InlineKeyboardButton(text="🚀 Заказать своего бота", url=site_url)])
    else:
        rows.append([button("🚀 Заказать своего бота", "order", generation)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_services(generation: int, services: tuple[Any, ...]) -> InlineKeyboardMarkup:
    rows = [
        [button(f"{item.name} · {item.price:.0f} ₽", "service", generation, item.id)]
        for item in services
    ]
    rows.append([button("← Главное меню", "menu", generation)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def slot_choices(
    generation: int,
    slots: tuple[Any, ...],
    format_date: Callable[[datetime], str],
) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                f"{format_date(item.start_at)} · {item.staff_name}",
                "book",
                generation,
                item.id,
            )
        ]
        for item in slots[:10]
    ]
    rows.append([button("← К услугам", "client", generation)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def master_menu(generation: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("📋 Записи", "appointments", generation),
                button("👥 Клиенты", "clients", generation),
            ],
            [
                button("🧾 Услуги", "services", generation),
                button("🗓 Расписание", "schedule", generation),
            ],
            [
                button("💳 Платежи", "payment_demo", generation),
                button("📣 Рассылки", "broadcast_demo", generation),
            ],
            [button("← Главное меню", "menu", generation)],
        ]
    )


def appointments_menu(generation: int, appointments: tuple[Any, ...]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in appointments[:8]:
        if item.status in {"confirmed", "client_confirmed"}:
            rows.append(
                [
                    button(f"✅ #{item.id}", "confirm", generation, item.id),
                    button(f"✖️ #{item.id}", "cancel", generation, item.id),
                ]
            )
    rows.append([button("← Панель мастера", "master", generation)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def services_menu(generation: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("＋ Добавить демо-услугу", "add_service", generation)],
            [button("← Панель мастера", "master", generation)],
        ]
    )


def schedule_menu(generation: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("＋ Добавить свободное окно", "add_window", generation)],
            [button("← Панель мастера", "master", generation)],
        ]
    )


def reset_confirmation(generation: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Да, сбросить мои данные", "reset", generation)],
            [button("Отмена", "menu", generation)],
        ]
    )
