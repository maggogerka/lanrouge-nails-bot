"""Payment list and confirmed human boundary for manual receipts."""

from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import PaymentMode, PaymentStatus
from app.domain.errors import DomainError
from app.domain.payments import PaymentStateError
from app.keyboards.admin.main import ADMIN_PAYMENTS_TEXT
from app.keyboards.admin.payments import (
    PaymentAdminCallback,
    manual_approval_confirmation,
    manual_instruction_preview_keyboard,
    manual_refund_confirmation,
    manual_rejection_reason_keyboard,
    payment_admin_details_keyboard,
    payment_admin_home_keyboard,
    payment_admin_list_keyboard,
    payment_admin_settings_keyboard,
    payment_mode_confirmation,
    refund_confirmation,
)
from app.keyboards.admin.services import cancel_keyboard
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.payment import PaymentAdminSection, PaymentAdminView
from app.services.payment_admin_service import PaymentAdministrationService
from app.states.payments import PaymentSettingsForm
from app.utils.telegram import edit_text_safely

router = Router(name="admin.payments")


def _render(payment: PaymentAdminView) -> str:
    zone = ZoneInfo(payment.timezone)
    appointment_at = payment.appointment_start_at.astimezone(zone)
    created_at = payment.created_at.astimezone(zone)
    expires_at = (
        payment.expires_at.astimezone(zone).strftime("%d.%m.%Y %H:%M")
        if payment.expires_at is not None
        else "—"
    )
    paid_at = (
        payment.paid_at.astimezone(zone).strftime("%d.%m.%Y %H:%M")
        if payment.paid_at is not None
        else "—"
    )
    username = f"@{escape(payment.client_username)}" if payment.client_username else "—"
    phone = escape(payment.client_phone) if payment.client_phone else "—"
    telegram_id = str(payment.client_telegram_id) if payment.client_telegram_id else "—"
    status = {
        "created": "создаётся",
        "pending": "ожидает оплаты/проверки",
        "succeeded": "подтверждена",
        "cancelled": "отменена",
        "failed": "отклонена или не прошла",
        "refund_pending": "возврат выполняется",
        "partially_refunded": "частично возвращена",
        "refunded": "полностью возвращена",
    }.get(payment.status.value, payment.status.value)
    manual_line = (
        f"Ручная проверка: {payment.manual_status.value}\n"
        if payment.manual_status is not None
        else ""
    )
    return (
        f"<b>Предоплата #{payment.id}</b>\n\n"
        f"Дата записи: {appointment_at:%d.%m.%Y %H:%M}\n"
        f"Услуга: {escape(payment.service_name)}\n"
        f"Мастер: {escape(payment.master_name)}\n"
        f"Клиент: {escape(payment.client_name)}\n"
        f"Телефон: {phone}\n"
        f"Telegram: {username}\n"
        f"Telegram ID: <code>{telegram_id}</code>\n\n"
        f"Сумма: {payment.amount:.2f} {payment.currency}\n"
        f"Статус: {status}\n"
        f"Способ: {payment.provider.value}\n"
        f"{manual_line}"
        f"Создана: {created_at:%d.%m.%Y %H:%M}\n"
        f"Резерв до: {expires_at}\n"
        f"Подтверждена: {paid_at}\n"
        f"Возвращено: {payment.refunded_amount:.2f} {payment.currency}\n"
        f"Запись: #{payment.appointment_id}"
    )


async def _show(
    message: Message,
    service: PaymentAdministrationService,
    actor: StaffContext,
) -> None:
    active = await service.list_panel(actor, PaymentAdminSection.ACTIVE, limit=100)
    history = await service.list_panel(actor, PaymentAdminSection.HISTORY, limit=100)
    settings = await service.get_settings(actor)
    await message.answer(
        f"<b>Предоплаты</b>\n\nРежим: {settings.mode.value}\n"
        f"Время на оплату: {settings.reservation_ttl_minutes} мин.\n"
        f"Инструкция ручной оплаты: "
        f"{'настроена' if settings.manual_payment_instructions else 'не настроена'}\n\n"
        "Действующие предоплаты и завершённые операции находятся в отдельных списках.",
        reply_markup=payment_admin_home_keyboard(
            active_count=len(active),
            history_count=len(history),
            can_configure=_can_configure(actor),
        ),
    )


def _can_configure(actor: StaffContext) -> bool:
    return any(
        actor.has_permission(permission)
        for permission in {
            StaffPermission.EDIT_PAYMENT_INSTRUCTIONS,
            StaffPermission.EDIT_PAYMENT_TIMERS,
            StaffPermission.CHANGE_PAYMENT_SETTINGS,
        }
    )


@router.message(F.text == ADMIN_PAYMENTS_TEXT)
async def show_payments(
    message: Message,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
) -> None:
    try:
        await _show(message, payment_admin_service, staff_context)
    except (DomainError, PaymentStateError) as exc:
        await message.answer(str(exc))


@router.callback_query(PaymentAdminCallback.filter(F.action == "list"))
async def refresh_payments(
    callback: CallbackQuery,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await _show(callback.message, payment_admin_service, staff_context)
    await callback.answer()


@router.callback_query(PaymentAdminCallback.filter(F.action.in_({"active", "history"})))
async def show_payment_section(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
) -> None:
    await state.clear()
    section = PaymentAdminSection(callback_data.action)
    try:
        payments = await payment_admin_service.list_panel(staff_context, section, limit=100)
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    title = (
        "Действующие предоплаты" if section is PaymentAdminSection.ACTIVE else "История предоплат"
    )
    text = f"<b>{title}</b>\n\n"
    text += (
        "Выберите предоплату по дате записи и клиенту."
        if payments
        else "В этом разделе пока нет операций."
    )
    if isinstance(callback.message, Message):
        await edit_text_safely(
            callback.message,
            text,
            reply_markup=payment_admin_list_keyboard(payments, section),
        )
    await callback.answer()


@router.callback_query(PaymentAdminCallback.filter(F.action == "settings"))
async def show_payment_settings(
    callback: CallbackQuery,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
) -> None:
    try:
        settings = await payment_admin_service.get_settings(staff_context)
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await edit_text_safely(
            callback.message,
            "<b>Настройки предоплаты</b>\n\n"
            f"Режим: {settings.mode.value}\n"
            f"Время на оплату: {settings.reservation_ttl_minutes} мин.\n"
            "Инструкция ручной оплаты: "
            f"{'настроена' if settings.manual_payment_instructions else 'не настроена'}",
            reply_markup=payment_admin_settings_keyboard(
                can_edit_instructions=staff_context.has_permission(
                    StaffPermission.EDIT_PAYMENT_INSTRUCTIONS
                ),
                can_edit_timers=staff_context.has_permission(StaffPermission.EDIT_PAYMENT_TIMERS),
                can_change_settings=staff_context.has_permission(
                    StaffPermission.CHANGE_PAYMENT_SETTINGS
                ),
            ),
        )
    await callback.answer()


@router.callback_query(PaymentAdminCallback.filter(F.action == "view"))
async def view_payment(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
) -> None:
    try:
        payment = await payment_admin_service.get_panel_payment(
            staff_context, callback_data.payment_id
        )
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    try:
        section = PaymentAdminSection(callback_data.mode)
    except ValueError:
        # Old messages created before the active/history split have mode="none".
        # Keep those buttons usable instead of exposing a validation error.
        section = (
            PaymentAdminSection.ACTIVE
            if payment.status
            in {PaymentStatus.CREATED, PaymentStatus.PENDING, PaymentStatus.REFUND_PENDING}
            else PaymentAdminSection.HISTORY
        )
    if isinstance(callback.message, Message):
        await edit_text_safely(
            callback.message,
            _render(payment),
            reply_markup=payment_admin_details_keyboard(
                payment,
                section=section,
                can_manage=staff_context.has_permission(StaffPermission.APPROVE_PREPAYMENTS),
                can_reject=staff_context.has_permission(StaffPermission.REJECT_PREPAYMENTS),
                can_refund=staff_context.has_permission(StaffPermission.REFUND_PAYMENTS),
            ),
        )
        receipt = await payment_admin_service.get_manual_receipt(
            staff_context, callback_data.payment_id
        )
        if receipt is not None:
            if receipt.media_type == "photo":
                await callback.message.answer_photo(receipt.telegram_file_id)
            else:
                await callback.message.answer_document(receipt.telegram_file_id)
    await callback.answer()


@router.callback_query(PaymentAdminCallback.filter(F.action == "approve_prompt"))
async def prompt_manual_approval(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Подтверждайте только после фактического поступления денег. "
            "Бот не запрашивает данные карты, CVV, SMS-коды или чеки с секретами.",
            reply_markup=manual_approval_confirmation(callback_data.payment_id),
        )
    await callback.answer()


@router.callback_query(PaymentAdminCallback.filter(F.action == "message_client"))
async def begin_client_message(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
) -> None:
    try:
        payment = await payment_admin_service.get_panel_payment(
            staff_context, callback_data.payment_id
        )
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.set_state(PaymentSettingsForm.client_message)
    await state.update_data(client_message_payment_id=payment.id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Введите сообщение для клиента {escape(payment.client_name)}. "
            "Бот доставит его от имени бизнеса. До 1000 символов.",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(PaymentSettingsForm.client_message)
async def send_client_message(
    message: Message,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    bot: Bot,
) -> None:
    text = (message.text or "").strip()
    if not 1 <= len(text) <= 1000:
        await message.answer("Введите текст сообщения длиной от 1 до 1000 символов.")
        return
    data = await state.get_data()
    payment_id = data.get("client_message_payment_id")
    if not isinstance(payment_id, int) or payment_id <= 0:
        await state.clear()
        await message.answer("Карточка предоплаты устарела. Откройте её заново.")
        return
    try:
        payment = await payment_admin_service.get_panel_payment(staff_context, payment_id)
        if payment.client_telegram_id is None:
            raise PaymentStateError("У клиента нет доступного Telegram ID.")
        await bot.send_message(
            payment.client_telegram_id,
            "<b>Сообщение по вашей записи</b>\n\n" + escape(text),
        )
    except (DomainError, PaymentStateError) as exc:
        await state.clear()
        await message.answer(str(exc))
        return
    except TelegramAPIError:
        await state.clear()
        await message.answer(
            "Telegram не смог доставить сообщение. Возможно, клиент заблокировал бота."
        )
        return
    await state.clear()
    await message.answer("Сообщение клиенту отправлено.")


@router.callback_query(PaymentAdminCallback.filter(F.action == "approve_confirm"))
async def approve_manual_payment(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
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
        payment = await payment_admin_service.get_panel_payment(
            staff_context, callback_data.payment_id
        )
        await callback.message.answer(
            _render(payment),
            reply_markup=payment_admin_details_keyboard(
                payment,
                section=PaymentAdminSection.HISTORY,
                can_manage=False,
                can_reject=False,
                can_refund=staff_context.has_permission(StaffPermission.REFUND_PAYMENTS),
            ),
        )
    await callback.answer("Ручная оплата подтверждена, запись активирована.")


@router.callback_query(PaymentAdminCallback.filter(F.action == "reject_prompt"))
async def prompt_manual_rejection(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    state: FSMContext,
) -> None:
    await state.set_state(PaymentSettingsForm.rejection_reason)
    await state.update_data(reject_manual_payment_id=callback_data.payment_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Укажите причину отклонения до 500 символов или выберите «Без комментария». "
            "Для совместимости можно отправить символ «-».",
            reply_markup=manual_rejection_reason_keyboard(callback_data.payment_id),
        )
    await callback.answer()


@router.callback_query(PaymentAdminCallback.filter(F.action == "reject_no_reason"))
async def reject_manual_without_reason(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    bot: Bot,
    correlation_id: str,
) -> None:
    await _reject_manual_payment(
        callback,
        payment_id=callback_data.payment_id,
        reason=None,
        service=payment_admin_service,
        actor=staff_context,
        bot=bot,
        correlation_id=correlation_id,
    )
    await state.clear()


@router.message(PaymentSettingsForm.rejection_reason)
async def reject_manual_with_reason(
    message: Message,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    bot: Bot,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    payment_id = data.get("reject_manual_payment_id")
    if not isinstance(payment_id, int) or payment_id <= 0:
        await state.clear()
        await message.answer("Черновик отклонения устарел.")
        return
    try:
        decision = await payment_admin_service.reject_manual(
            staff_context,
            payment_id,
            reason=message.text,
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer("Предоплата отклонена, запись отменена, окно снова доступно.")
    if decision.changed:
        await _notify_rejection(bot, decision.client_telegram_id, decision.payment.rejection_reason)


@router.callback_query(PaymentAdminCallback.filter(F.action == "refund_prompt"))
async def prompt_refund(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Оформить возврат всей ещё не возвращённой суммы? "
            "Операция будет идемпотентной и попадёт в аудит.",
            reply_markup=refund_confirmation(callback_data.payment_id),
        )
    await callback.answer()


@router.callback_query(PaymentAdminCallback.filter(F.action == "refund_confirm"))
async def create_refund(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        outcome = await payment_admin_service.request_remaining_refund(
            staff_context,
            callback_data.payment_id,
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    text = (
        f"Возврат #{outcome.refund.id}: {outcome.refund.amount:.2f} "
        f"{outcome.refund.currency}, статус {outcome.refund.status.value}."
    )
    keyboard = (
        manual_refund_confirmation(outcome.refund)
        if outcome.refund.provider is PaymentMode.MANUAL
        and outcome.refund.status.value == "pending"
        else None
    )
    if isinstance(callback.message, Message):
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer("Возврат создан.")


@router.callback_query(PaymentAdminCallback.filter(F.action == "manual_refund_confirm"))
async def approve_manual_refund(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        refund = await payment_admin_service.approve_manual_refund(
            staff_context,
            callback_data.payment_id,
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Возврат #{refund.id} подтверждён: {refund.amount:.2f} {refund.currency}."
        )
    await callback.answer("Ручной возврат подтверждён.")


@router.callback_query(PaymentAdminCallback.filter(F.action == "edit_manual_instructions"))
async def begin_manual_instructions(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(PaymentSettingsForm.manual_instructions)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите инструкцию для клиента: СБП-телефон, безопасную платёжную ссылку "
            "или текст. Не запрашивайте номер карты, CVV/CVC, срок действия и SMS-коды.",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(PaymentSettingsForm.manual_instructions)
async def preview_manual_instructions(
    message: Message,
    state: FSMContext,
) -> None:
    instructions = (message.text or "").strip()
    forbidden = ("cvv", "cvc", "sms-код", "смс-код", "срок действия карты")
    if not 1 <= len(instructions) <= 2000 or any(
        marker in instructions.casefold() for marker in forbidden
    ):
        await message.answer(
            "Инструкция должна содержать 1–2000 символов и не запрашивать "
            "CVV/CVC, SMS-коды или срок действия карты."
        )
        return
    await state.update_data(manual_instructions_preview=instructions)
    await state.set_state(PaymentSettingsForm.manual_instructions_preview)
    await message.answer(
        f"<b>Предварительный просмотр инструкции</b>\n\n{escape(instructions)}",
        reply_markup=manual_instruction_preview_keyboard(),
    )


@router.callback_query(
    PaymentSettingsForm.manual_instructions_preview,
    PaymentAdminCallback.filter(F.action == "instructions_save"),
)
async def save_manual_instructions(
    callback: CallbackQuery,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    instructions = data.get("manual_instructions_preview")
    if not isinstance(instructions, str):
        await state.clear()
        await callback.answer("Предварительный просмотр устарел.", show_alert=True)
        return
    try:
        await payment_admin_service.set_manual_instructions(
            staff_context,
            instructions,
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("Инструкция ручной оплаты сохранена.")
        await _show(callback.message, payment_admin_service, staff_context)
    await callback.answer("Сохранено.")


@router.callback_query(PaymentAdminCallback.filter(F.action == "edit_payment_timer"))
async def begin_payment_timer_edit(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(PaymentSettingsForm.reservation_ttl)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите время для оплаты от 5 до 60 минут. "
            "Для значения 15 напоминания отправляются через 5 и 10 минут.",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(PaymentSettingsForm.reservation_ttl)
async def save_payment_timer(
    message: Message,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdecimal():
        await message.answer("Введите целое число минут от 5 до 60.")
        return
    try:
        settings = await payment_admin_service.set_reservation_ttl(
            staff_context,
            int(raw),
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    reminders = ", ".join(str(value) for value in settings.client_payment_reminder_minutes)
    await message.answer(
        f"Таймер оплаты: {settings.reservation_ttl_minutes} мин. "
        f"Напоминания клиенту: {reminders or 'отключены'}."
    )


@router.callback_query(PaymentAdminCallback.filter(F.action == "mode_prompt"))
async def prompt_payment_mode(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
) -> None:
    try:
        mode = PaymentMode(callback_data.mode)
    except ValueError:
        await callback.answer("Некорректный режим.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Изменить режим оплаты на «{mode.value}»? "
            "Новые записи сразу начнут использовать эту конфигурацию.",
            reply_markup=payment_mode_confirmation(mode),
        )
    await callback.answer()


@router.callback_query(PaymentAdminCallback.filter(F.action == "mode_confirm"))
async def change_payment_mode(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        mode = PaymentMode(callback_data.mode)
        await payment_admin_service.set_mode(
            staff_context,
            mode,
            confirmed=True,
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Режим оплаты изменён.")
    if isinstance(callback.message, Message):
        await _show(callback.message, payment_admin_service, staff_context)


async def _reject_manual_payment(
    callback: CallbackQuery,
    *,
    payment_id: int,
    reason: str | None,
    service: PaymentAdministrationService,
    actor: StaffContext,
    bot: Bot,
    correlation_id: str,
) -> None:
    try:
        decision = await service.reject_manual(
            actor,
            payment_id,
            reason=reason,
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer("Предоплата отклонена, запись отменена, окно снова доступно.")
    if decision.changed:
        await _notify_rejection(
            bot,
            decision.client_telegram_id,
            decision.payment.rejection_reason,
        )
    await callback.answer("Предоплата отклонена.")


async def _notify_rejection(
    bot: Bot,
    telegram_id: int,
    reason: str | None,
) -> None:
    reason_line = f"\nПричина: {escape(reason)}" if reason else ""
    try:
        await bot.send_message(
            telegram_id,
            f"Предоплата не подтверждена. Запись отменена, резерв времени освобождён.{reason_line}",
        )
    except TelegramAPIError:
        return
