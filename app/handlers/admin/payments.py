"""Payment list and confirmed human boundary for manual receipts."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import PaymentMode
from app.domain.errors import DomainError
from app.domain.payments import PaymentStateError
from app.keyboards.admin.main import ADMIN_PAYMENTS_TEXT
from app.keyboards.admin.payments import (
    PaymentAdminCallback,
    manual_approval_confirmation,
    manual_refund_confirmation,
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
    return (
        f"<b>Платёж #{payment.id}</b>\n"
        f"Запись: #{payment.appointment_id}\n"
        f"Режим: {payment.provider.value}\n"
        f"Сумма: {payment.amount:.2f} {payment.currency}\n"
        f"Статус: {payment.status.value}\n"
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
        f"Последние операции: {len(payments)}",
        reply_markup=payments_keyboard(
            payments,
            can_manage=actor.has_permission(StaffPermission.MANAGE_PAYMENTS),
            can_refund=actor.has_permission(StaffPermission.REFUND_PAYMENTS),
            can_configure=actor.has_permission(StaffPermission.MANAGE_PRIVATE_SETTINGS),
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
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
) -> None:
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
async def save_manual_instructions(
    message: Message,
    state: FSMContext,
    payment_admin_service: PaymentAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        await payment_admin_service.set_manual_instructions(
            staff_context,
            message.text or "",
            correlation_id=correlation_id,
        )
    except (DomainError, PaymentStateError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer("Инструкция ручной оплаты сохранена.")
    await _show(message, payment_admin_service, staff_context)


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
