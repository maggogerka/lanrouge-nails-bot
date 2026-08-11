"""Payment list and confirmed human boundary for manual receipts."""

from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import PaymentMode
from app.domain.errors import DomainError
from app.domain.payments import PaymentStateError
from app.keyboards.admin.main import ADMIN_PAYMENTS_TEXT
from app.keyboards.admin.payments import (
    PaymentAdminCallback,
    manual_approval_confirmation,
    manual_instruction_preview_keyboard,
    manual_refund_confirmation,
    manual_rejection_reason_keyboard,
    payment_mode_confirmation,
    payments_keyboard,
    refund_confirmation,
)
from app.keyboards.admin.services import cancel_keyboard
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.payment import PaymentView
from app.services.payment_admin_service import PaymentAdministrationService
from app.states.payments import PaymentSettingsForm

router = Router(name="admin.payments")


def _render(payment: PaymentView) -> str:
    manual_line = (
        f"Ручной статус: {payment.manual_status.value}\n"
        if payment.manual_status is not None
        else ""
    )
    return (
        f"<b>Платёж #{payment.id}</b>\n"
        f"Запись: #{payment.appointment_id}\n"
        f"Режим: {payment.provider.value}\n"
        f"Сумма: {payment.amount:.2f} {payment.currency}\n"
        f"Статус: {payment.status.value}\n"
        f"{manual_line}"
        f"Возвращено: {payment.refunded_amount:.2f} {payment.currency}"
    )


async def _show(
    message: Message,
    service: PaymentAdministrationService,
    actor: StaffContext,
) -> None:
    payments = await service.list_recent(actor)
    settings = await service.get_settings(actor)
    await message.answer(
        f"<b>Оплата</b>\nРежим: {settings.mode.value}\n"
        f"Резерв: {settings.reservation_ttl_minutes} мин.\n"
        f"Инструкция ручной оплаты: "
        f"{'настроена' if settings.manual_payment_instructions else 'не настроена'}\n"
        + (f"Последние операции: {len(payments)}" if payments else "Предоплат пока нет."),
        reply_markup=payments_keyboard(
            payments,
            can_manage=actor.has_permission(StaffPermission.APPROVE_PREPAYMENTS),
            can_reject=actor.has_permission(StaffPermission.REJECT_PREPAYMENTS),
            can_refund=actor.has_permission(StaffPermission.REFUND_PAYMENTS),
            can_edit_instructions=actor.has_permission(StaffPermission.EDIT_PAYMENT_INSTRUCTIONS),
            can_edit_timers=actor.has_permission(StaffPermission.EDIT_PAYMENT_TIMERS),
            can_change_settings=actor.has_permission(StaffPermission.CHANGE_PAYMENT_SETTINGS),
        ),
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


@router.callback_query(PaymentAdminCallback.filter(F.action == "view"))
async def view_payment(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
) -> None:
    payments = await payment_admin_service.list_recent(staff_context, limit=100)
    payment = next((item for item in payments if item.id == callback_data.payment_id), None)
    if payment is None:
        await callback.answer("Платёж не найден.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(_render(payment))
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


@router.callback_query(PaymentAdminCallback.filter(F.action == "approve_confirm"))
async def approve_manual_payment(
    callback: CallbackQuery,
    callback_data: PaymentAdminCallback,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        payment = await payment_admin_service.approve_manual(
            staff_context,
            callback_data.payment_id,
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(_render(payment))
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
