"""Minimal master panel with no unscoped legacy admin actions."""

from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.errors import AppointmentNotFoundError, AppointmentStateError, DomainError
from app.domain.payments import PaymentStateError
from app.keyboards.master.main import (
    MASTER_APPOINTMENTS_TEXT,
    MASTER_PREPAYMENTS_TEXT,
    MASTER_SCHEDULE_TEXT,
    MASTER_SUPPORT_TEXT,
    master_main_keyboard,
)
from app.keyboards.master.workspace import (
    MasterAppointmentCallback,
    MasterPaymentCallback,
    MasterScheduleCallback,
    master_appointment_actions,
    master_appointment_confirmation,
    master_payment_actions,
    master_payment_confirmation,
    master_schedule_actions,
)
from app.keyboards.support import vendor_support_keyboard
from app.schemas.authorization import StaffContext
from app.schemas.master_workspace import (
    MasterAppointmentView,
    MasterScheduleView,
)
from app.schemas.payment import PaymentView
from app.services.master_workspace_service import MasterWorkspaceService
from app.services.payment_admin_service import PaymentAdministrationService
from app.services.presentation_service import PresentationService
from app.services.vendor_support_service import VendorSupportService

router = Router(name="master.menu")


@router.message(Command("master"))
async def show_master_menu(
    message: Message,
    state: FSMContext,
    staff_context: StaffContext,
    presentation_service: PresentationService,
) -> None:
    """Open a role-specific panel without exposing admin identifiers or callbacks."""

    await state.clear()
    business = await presentation_service.get_business()
    await message.answer(
        f"<b>{escape(business.display_name)}</b> · мастер "
        f"<b>{escape(staff_context.display_name)}</b>.",
        reply_markup=master_main_keyboard(staff_context),
    )


@router.message(F.text == MASTER_APPOINTMENTS_TEXT)
async def show_own_appointments(
    message: Message,
    staff_context: StaffContext,
    master_workspace_service: MasterWorkspaceService,
) -> None:
    appointments = await master_workspace_service.list_workspace_appointments(staff_context)
    await message.answer(
        _render_appointments(appointments),
        reply_markup=master_appointment_actions(appointments),
    )


@router.callback_query(
    MasterAppointmentCallback.filter(
        F.action.in_({"request_complete", "request_no_show", "request_cancel"})
    )
)
async def request_own_appointment_action(
    callback: CallbackQuery,
    callback_data: MasterAppointmentCallback,
) -> None:
    action = callback_data.action.removeprefix("request_")
    prompts = {
        "complete": "Завершить визит",
        "no_show": "Отметить неявку",
        "cancel": "Отменить будущую запись",
    }
    prompt = prompts.get(action)
    if prompt is None:
        await callback.answer("Действие устарело.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"{prompt} №{callback_data.appointment_id}?",
            reply_markup=master_appointment_confirmation(
                action=action,
                appointment_id=callback_data.appointment_id,
            ),
        )
    await callback.answer()


@router.callback_query(MasterAppointmentCallback.filter(F.action == "dismiss"))
async def dismiss_own_appointment_action(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Действие отменено.")
    await callback.answer()


@router.callback_query(
    MasterAppointmentCallback.filter(
        F.action.in_({"confirm_complete", "confirm_no_show", "confirm_cancel"})
    )
)
async def apply_own_appointment_action(
    callback: CallbackQuery,
    callback_data: MasterAppointmentCallback,
    staff_context: StaffContext,
    master_workspace_service: MasterWorkspaceService,
    correlation_id: str,
) -> None:
    action = callback_data.action.removeprefix("confirm_")
    try:
        if action == "complete":
            result = await master_workspace_service.complete_own_visit(
                staff_context,
                callback_data.appointment_id,
                correlation_id=correlation_id,
            )
        elif action == "no_show":
            result = await master_workspace_service.mark_own_no_show(
                staff_context,
                callback_data.appointment_id,
                correlation_id=correlation_id,
            )
        elif action == "cancel":
            result = await master_workspace_service.cancel_own_appointment(
                staff_context,
                callback_data.appointment_id,
                correlation_id=correlation_id,
            )
        else:
            await callback.answer("Действие устарело.", show_alert=True)
            return
    except (AppointmentNotFoundError, AppointmentStateError) as error:
        await callback.answer(str(error), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"Запись №{result.appointment_id}: <code>{result.status.value}</code>."
        )
    await callback.answer("Готово.")


@router.message(F.text == MASTER_SCHEDULE_TEXT)
async def show_own_schedule(
    message: Message,
    staff_context: StaffContext,
    master_workspace_service: MasterWorkspaceService,
) -> None:
    schedule = await master_workspace_service.get_schedule(staff_context)
    await message.answer(
        _render_schedule(schedule),
        reply_markup=master_schedule_actions(is_paused=schedule.paused_until is not None),
    )


@router.message(F.text == MASTER_PREPAYMENTS_TEXT)
async def show_own_prepayments(
    message: Message,
    staff_context: StaffContext,
    payment_admin_service: PaymentAdministrationService,
) -> None:
    try:
        payments = await payment_admin_service.list_recent(staff_context, limit=50)
    except (DomainError, PaymentStateError) as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        _render_prepayments(payments),
        reply_markup=master_payment_actions(payments),
    )


@router.callback_query(MasterPaymentCallback.filter(F.action == "approve_prompt"))
async def prompt_own_prepayment_approval(
    callback: CallbackQuery,
    callback_data: MasterPaymentCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Подтверждайте предоплату только после фактического поступления денег.",
            reply_markup=master_payment_confirmation(callback_data.payment_id),
        )
    await callback.answer()


@router.callback_query(MasterPaymentCallback.filter(F.action == "approve_confirm"))
async def approve_own_prepayment(
    callback: CallbackQuery,
    callback_data: MasterPaymentCallback,
    staff_context: StaffContext,
    payment_admin_service: PaymentAdministrationService,
    correlation_id: str,
) -> None:
    try:
        await payment_admin_service.approve_manual(
            staff_context,
            callback_data.payment_id,
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Предоплата подтверждена.")
    await callback.answer("Запись активирована.")


@router.callback_query(MasterPaymentCallback.filter(F.action == "dismiss"))
async def dismiss_own_prepayment(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Действие отменено.")
    await callback.answer()


@router.callback_query(
    MasterScheduleCallback.filter(F.action.in_({"pause_1", "pause_7", "resume"}))
)
async def change_own_schedule_pause(
    callback: CallbackQuery,
    callback_data: MasterScheduleCallback,
    staff_context: StaffContext,
    master_workspace_service: MasterWorkspaceService,
    correlation_id: str,
) -> None:
    pause_days = {"pause_1": 1, "pause_7": 7, "resume": None}.get(callback_data.action)
    if callback_data.action not in {"pause_1", "pause_7", "resume"}:
        await callback.answer("Действие устарело.", show_alert=True)
        return
    schedule = await master_workspace_service.set_schedule_pause(
        staff_context,
        pause_days=pause_days,
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_schedule(schedule),
            reply_markup=master_schedule_actions(is_paused=schedule.paused_until is not None),
        )
    await callback.answer("Настройка сохранена.")


@router.message(F.text == MASTER_SUPPORT_TEXT)
async def show_master_support(
    message: Message,
    staff_context: StaffContext,
    vendor_support_service: VendorSupportService,
    correlation_id: str,
) -> None:
    text, url = vendor_support_service.render(
        staff_context,
        correlation_id=correlation_id,
    )
    await message.answer(text, reply_markup=vendor_support_keyboard(url))


def _render_appointments(items: tuple[MasterAppointmentView, ...]) -> str:
    if not items:
        return "Ближайших записей у вас нет."
    rows = ["<b>Мои ближайшие записи</b>"]
    for item in items:
        local = item.start_at.astimezone(ZoneInfo(item.timezone))
        phone = f" · {escape(item.client_phone)}" if item.client_phone else ""
        rows.append(
            f"№{item.appointment_id} · {local:%d.%m %H:%M}\n"
            f"{escape(item.service_name)} · {escape(item.client_name)}{phone}\n"
            f"Статус: <code>{item.status.value}</code>"
        )
    return "\n\n".join(rows)


def _render_prepayments(items: tuple[PaymentView, ...]) -> str:
    pending = [
        item
        for item in items
        if item.manual_status is not None
        and item.manual_status.value in {"client_reported", "review_pending"}
    ]
    if not pending:
        return "Предоплат по вашим записям, ожидающих проверки, нет."
    rows = ["<b>Предоплаты моих записей</b>"]
    rows.extend(
        f"№{item.id} · запись №{item.appointment_id} · "
        f"{item.amount:.2f} {escape(item.currency)}"
        + (" · чек приложен" if item.has_receipt else "")
        for item in pending
    )
    return "\n".join(rows)


def _render_schedule(schedule: MasterScheduleView) -> str:
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    rows = ["<b>Моё расписание</b>"]
    if schedule.paused_until is not None:
        local_pause = schedule.paused_until.astimezone(ZoneInfo(schedule.timezone))
        rows.append(f"Онлайн-запись приостановлена до {local_pause:%d.%m.%Y %H:%M}.")
    else:
        rows.append("Онлайн-запись активна.")
    if schedule.weekly_intervals:
        rows.append("<b>Еженедельный шаблон:</b>")
        rows.extend(
            f"{weekdays[item.weekday]} · {_minute(item.start_minute)}–"
            f"{_minute(item.end_minute)} · {item.kind.value}"
            for item in schedule.weekly_intervals
        )
    else:
        rows.append("Еженедельный шаблон ещё не настроен владельцем.")
    if schedule.upcoming_exceptions:
        rows.append("<b>Ближайшие исключения:</b>")
        rows.extend(
            f"{item.local_date:%d.%m.%Y} · {item.kind.value}"
            + (
                f" · {_minute(item.start_minute)}–{_minute(item.end_minute)}"
                if item.start_minute is not None and item.end_minute is not None
                else ""
            )
            + (f" · {escape(item.reason)}" if item.reason else "")
            for item in schedule.upcoming_exceptions
        )
    return "\n".join(rows)


def _minute(value: int) -> str:
    return f"{value // 60:02}:{value % 60:02}"
