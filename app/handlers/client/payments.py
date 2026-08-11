"""Manual prepayment reporting and optional receipt upload."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.enums import StaffRole
from app.domain.errors import DomainError
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.payments import ManualPaymentCallback, receipt_choice_keyboard
from app.schemas.authorization import StaffPermission
from app.schemas.payment import ManualReceiptDraft
from app.services.authorization_service import AuthorizationService
from app.services.manual_prepayment_service import ManualPrepaymentService
from app.states.payments import ManualReceiptUpload

router = Router(name="client.payments")
_PAYMENT_STAFF_ROLES = frozenset(StaffRole)
_ALLOWED_DOCUMENT_MIME_TYPES = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/webp"}
)


@router.callback_query(ManualPaymentCallback.filter(F.action == "paid"))
async def report_manual_payment(
    callback: CallbackQuery,
    callback_data: ManualPaymentCallback,
    manual_prepayment_service: ManualPrepaymentService,
    authorization_service: AuthorizationService,
    bot: Bot,
    correlation_id: str,
) -> None:
    try:
        outcome = await manual_prepayment_service.report_paid(
            actor_from_telegram(callback.from_user),
            callback_data.payment_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if outcome.changed:
        staff = await authorization_service.list_active_staff(
            business_id=outcome.payment.business_id,
            roles=_PAYMENT_STAFF_ROLES,
        )
        for recipient in staff:
            if not recipient.has_permission(StaffPermission.VIEW_PREPAYMENTS):
                continue
            try:
                await bot.send_message(
                    recipient.telegram_id,
                    f"Клиент сообщил об оплате платежа #{outcome.payment.id}. "
                    "Откройте раздел «Оплата» для проверки.",
                )
            except TelegramAPIError:
                continue
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Сообщение об оплате принято. Чек можно приложить по желанию.",
            reply_markup=receipt_choice_keyboard(outcome.payment.id),
        )
    await callback.answer("Таймер остановлен. Ожидаем проверку сотрудника.")


@router.callback_query(ManualPaymentCallback.filter(F.action == "attach"))
async def begin_receipt_upload(
    callback: CallbackQuery,
    callback_data: ManualPaymentCallback,
    state: FSMContext,
) -> None:
    await state.set_state(ManualReceiptUpload.waiting)
    await state.update_data(manual_receipt_payment_id=callback_data.payment_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отправьте одно фото чека или документ PDF/JPEG/PNG/WEBP до 20 МБ. "
            "Данные карты, CVV и SMS-коды присылать нельзя."
        )
    await callback.answer()


@router.callback_query(ManualPaymentCallback.filter(F.action == "skip"))
async def skip_receipt_upload(
    callback: CallbackQuery,
    callback_data: ManualPaymentCallback,
    state: FSMContext,
    manual_prepayment_service: ManualPrepaymentService,
    correlation_id: str,
) -> None:
    try:
        await manual_prepayment_service.submit_for_review(
            actor_from_telegram(callback.from_user),
            callback_data.payment_id,
            receipt=None,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("Предоплата передана сотруднику на проверку.")
    await callback.answer("Готово.")


@router.message(ManualReceiptUpload.waiting)
async def save_receipt_upload(
    message: Message,
    state: FSMContext,
    manual_prepayment_service: ManualPrepaymentService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    payment_id = data.get("manual_receipt_payment_id")
    if not isinstance(payment_id, int) or payment_id <= 0:
        await state.clear()
        await message.answer("Черновик загрузки устарел. Откройте предоплату заново.")
        return
    try:
        receipt = _receipt_from_message(message)
        await manual_prepayment_service.submit_for_review(
            actor_from_telegram(message.from_user),
            payment_id,
            receipt=receipt,
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer("Чек сохранён и предоплата передана сотруднику на проверку.")


def _receipt_from_message(message: Message) -> ManualReceiptDraft:
    if message.photo:
        photo = message.photo[-1]
        if photo.file_size is None:
            raise ValueError("Telegram не сообщил размер файла. Отправьте другое фото.")
        return ManualReceiptDraft(
            telegram_file_id=photo.file_id,
            telegram_file_unique_id=photo.file_unique_id,
            media_type="photo",
            file_size=photo.file_size,
        )
    document = message.document
    if (
        document is None
        or document.mime_type not in _ALLOWED_DOCUMENT_MIME_TYPES
        or document.file_size is None
    ):
        raise ValueError("Нужны фото или документ PDF/JPEG/PNG/WEBP до 20 МБ.")
    return ManualReceiptDraft(
        telegram_file_id=document.file_id,
        telegram_file_unique_id=document.file_unique_id,
        media_type="document",
        file_size=document.file_size,
    )
