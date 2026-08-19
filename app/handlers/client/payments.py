"""Manual prepayment reporting and optional receipt upload."""

from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.enums import ManualPaymentStatus, PaymentMode, PaymentStatus, StaffRole
from app.domain.errors import DomainError
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.main import CLIENT_PAYMENTS_TEXT
from app.keyboards.client.payments import (
    ClientPaymentsCallback,
    ManualPaymentCallback,
    client_payment_details_keyboard,
    client_payments_home_keyboard,
    client_payments_list_keyboard,
    receipt_choice_keyboard,
)
from app.schemas.authorization import StaffPermission
from app.schemas.booking import ClientActor
from app.schemas.pagination import PageRequest
from app.schemas.payment import ClientPaymentSection, ClientPaymentView, ManualReceiptDraft
from app.services.authorization_service import AuthorizationService
from app.services.client_payment_service import ClientPaymentService
from app.services.manual_prepayment_service import ManualPrepaymentService
from app.states.payments import ManualReceiptUpload
from app.utils.telegram import edit_text_safely

router = Router(name="client.payments")
_PAYMENT_STAFF_ROLES = frozenset(StaffRole)
_ALLOWED_DOCUMENT_MIME_TYPES = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/webp"}
)
_CLIENT_PAYMENTS_PAGE_SIZE = 7
_PAYMENT_STATUS_LABELS = {
    PaymentStatus.CREATED: "создана",
    PaymentStatus.PENDING: "ожидает оплаты или проверки",
    PaymentStatus.SUCCEEDED: "оплачена",
    PaymentStatus.CANCELLED: "отменена",
    PaymentStatus.FAILED: "не прошла",
    PaymentStatus.REFUND_PENDING: "возврат обрабатывается",
    PaymentStatus.PARTIALLY_REFUNDED: "частично возвращена",
    PaymentStatus.REFUNDED: "возвращена",
}
_MANUAL_STATUS_LABELS = {
    ManualPaymentStatus.AWAITING_PAYMENT: "ожидается перевод",
    ManualPaymentStatus.CLIENT_REPORTED: "клиент сообщил об оплате",
    ManualPaymentStatus.REVIEW_PENDING: "проверяется сотрудником",
    ManualPaymentStatus.CONFIRMED: "подтверждена",
    ManualPaymentStatus.REJECTED: "отклонена",
    ManualPaymentStatus.EXPIRED: "срок истёк",
    ManualPaymentStatus.CANCELLED: "отменена",
}


async def _payment_home(
    message: Message,
    actor: ClientActor,
    client_payment_service: ClientPaymentService,
    *,
    edit: bool,
) -> None:
    active_count, history_count = await client_payment_service.get_my_counts(actor)
    text = (
        "<b>💳 Мои оплаты</b>\n\n"
        "Здесь хранятся только ваши предоплаты и платежи: текущие инструкции, "
        "статус проверки и история возвратов.\n\n"
        "Никому не отправляйте CVV, SMS-коды и полный номер карты."
    )
    markup = client_payments_home_keyboard(active_count=active_count, history_count=history_count)
    if edit:
        await edit_text_safely(message, text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == CLIENT_PAYMENTS_TEXT)
async def show_client_payments(
    message: Message,
    client_payment_service: ClientPaymentService,
) -> None:
    if message.from_user is None:
        return
    await _payment_home(
        message, actor_from_telegram(message.from_user), client_payment_service, edit=False
    )


@router.callback_query(ClientPaymentsCallback.filter(F.action == "home"))
async def refresh_client_payments(
    callback: CallbackQuery,
    client_payment_service: ClientPaymentService,
) -> None:
    if isinstance(callback.message, Message):
        await _payment_home(
            callback.message,
            actor_from_telegram(callback.from_user),
            client_payment_service,
            edit=True,
        )
    await callback.answer()


@router.callback_query(ClientPaymentsCallback.filter(F.action.in_({"active", "history"})))
async def list_client_payments(
    callback: CallbackQuery,
    callback_data: ClientPaymentsCallback,
    client_payment_service: ClientPaymentService,
) -> None:
    section = ClientPaymentSection(callback_data.action)
    result = await client_payment_service.list_my_page(
        actor_from_telegram(callback.from_user),
        section,
        PageRequest(page=callback_data.page, page_size=_CLIENT_PAYMENTS_PAGE_SIZE),
    )
    if result.total and result.page > result.pages:
        result = await client_payment_service.list_my_page(
            actor_from_telegram(callback.from_user),
            section,
            PageRequest(page=result.pages, page_size=_CLIENT_PAYMENTS_PAGE_SIZE),
        )
    title = "Действующие оплаты" if section is ClientPaymentSection.ACTIVE else "История оплат"
    text = f"<b>{title}</b>\n\n" + (
        "Выберите оплату, чтобы увидеть запись, сумму, статус и доступные действия."
        if result.items
        else "В этом разделе пока нет оплат."
    )
    if isinstance(callback.message, Message):
        await edit_text_safely(
            callback.message,
            text,
            reply_markup=client_payments_list_keyboard(
                result.items, section, page=result.page, pages=result.pages
            ),
        )
    await callback.answer()


def _payment_details_text(payment: ClientPaymentView) -> str:
    zone = ZoneInfo(payment.timezone)
    appointment_at = payment.appointment_start_at.astimezone(zone)
    created_at = payment.created_at.astimezone(zone)
    lines = [
        f"<b>Оплата #{payment.id}</b>",
        "",
        f"Услуга: {escape(payment.service_name)}",
        f"Мастер: {escape(payment.master_name)}",
        f"Запись: {appointment_at:%d.%m.%Y %H:%M}",
        f"Создана: {created_at:%d.%m.%Y %H:%M}",
        f"Сумма: {payment.amount:.2f} {escape(payment.currency)}",
        f"Статус: {_PAYMENT_STATUS_LABELS[payment.status]}",
        "Способ: ручной перевод" if payment.provider is PaymentMode.MANUAL else "Способ: YooKassa",
    ]
    if payment.manual_status is not None:
        lines.append(f"Проверка: {_MANUAL_STATUS_LABELS[payment.manual_status]}")
    if payment.refunded_amount > 0:
        lines.append(f"Возвращено: {payment.refunded_amount:.2f} {escape(payment.currency)}")
    if payment.paid_at is not None:
        lines.append(f"Оплачена: {payment.paid_at.astimezone(zone):%d.%m.%Y %H:%M}")
    if payment.expires_at is not None and payment.status in {
        PaymentStatus.CREATED,
        PaymentStatus.PENDING,
    }:
        lines.append(f"Оплатить до: {payment.expires_at.astimezone(zone):%d.%m.%Y %H:%M}")
    if payment.manual_payment_instructions:
        lines.extend(
            [
                "",
                "<b>Инструкция для перевода</b>",
                escape(payment.manual_payment_instructions),
            ]
        )
    if payment.rejection_reason:
        lines.append(f"Причина отклонения: {escape(payment.rejection_reason)}")
    return "\n".join(lines)


@router.callback_query(ClientPaymentsCallback.filter(F.action == "view"))
async def show_client_payment_details(
    callback: CallbackQuery,
    callback_data: ClientPaymentsCallback,
    client_payment_service: ClientPaymentService,
) -> None:
    try:
        payment = await client_payment_service.get_my_details(
            actor_from_telegram(callback.from_user), callback_data.payment_id
        )
        section = ClientPaymentSection(callback_data.section)
    except (DomainError, ValueError) as exc:
        await callback.answer(str(exc) or "Оплата не найдена.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await edit_text_safely(
            callback.message,
            _payment_details_text(payment),
            reply_markup=client_payment_details_keyboard(
                payment, section=section, page=callback_data.page
            ),
        )
    await callback.answer()


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
                    f"Клиент сообщил о предоплате #{outcome.payment.id}. "
                    "Откройте раздел «Предоплаты» для проверки.",
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
