"""Client details, confirmation and transactional booking handlers."""

from __future__ import annotations

import logging
from datetime import date
from secrets import token_urlsafe

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message, ReplyKeyboardRemove
from pydantic import ValidationError

from app.domain.booking import normalize_phone
from app.domain.enums import AppointmentStatus, PaymentMode
from app.domain.errors import BookingConflictError, DomainError
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.handlers.client.booking_common import (
    available_dates,
    render_admin_new_booking,
    render_booking_confirmation,
    render_booking_receipt,
)
from app.handlers.client.common import actor_from_telegram
from app.keyboards.client.booking import (
    BOOKING_BACK_TEXT,
    BOOKING_CANCEL_TEXT,
    BookingCallback,
    BookingReferenceCallback,
    appointment_links_keyboard,
    booking_navigation_keyboard,
    confirmation_keyboard,
    dates_keyboard,
    reference_media_keyboard,
    services_keyboard,
    windows_keyboard,
)
from app.keyboards.client.main import client_main_keyboard
from app.logging import log_event
from app.schemas.booking import BookingRequest, ReferenceMediaDraft
from app.security import LEGACY_ADMIN_ROLES
from app.services.authorization_service import AuthorizationService
from app.services.booking_service import BookingService
from app.services.menu_service import MenuService
from app.states.booking import BookingFlow

router = Router(name="client.booking_details")
logger = logging.getLogger(__name__)


@router.callback_query(BookingCallback.filter(F.action == "cancel"))
async def cancel_booking_callback(
    callback: CallbackQuery, state: FSMContext, menu_service: MenuService
) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Оформление записи отменено.")
        await callback.message.answer(
            "Главное меню:",
            reply_markup=client_main_keyboard(await menu_service.get_capabilities()),
        )
    await callback.answer()


@router.message(
    StateFilter(*BookingFlow.__all_states__),
    F.text == BOOKING_CANCEL_TEXT,
)
async def cancel_booking_message(
    message: Message, state: FSMContext, menu_service: MenuService
) -> None:
    await state.clear()
    await message.answer(
        "Оформление записи отменено.",
        reply_markup=client_main_keyboard(await menu_service.get_capabilities()),
    )


@router.message(
    StateFilter(BookingFlow.name, BookingFlow.phone, BookingFlow.comment),
    F.text == BOOKING_BACK_TEXT,
)
async def booking_back_message(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    if message.from_user is None:
        return
    current_state = await state.get_state()
    data = await state.get_data()
    if current_state == BookingFlow.name.state:
        try:
            service_id = int(data["service_id"])
            local_date = date.fromisoformat(str(data["local_date"]))
            staff_member_id = (
                int(data["staff_member_id"]) if data.get("staff_member_id") is not None else None
            )
            availability = await booking_service.list_availability(
                actor_from_telegram(message.from_user),
                service_id,
                staff_member_id=staff_member_id,
                local_date=local_date,
            )
        except (DomainError, KeyError, ValueError) as exc:
            await message.answer(str(exc))
            return
        await state.set_state(BookingFlow.window)
        await message.answer(
            f"Выберите время на {local_date:%d.%m.%Y}:",
            reply_markup=windows_keyboard(availability.windows, local_date),
        )
    elif current_state == BookingFlow.phone.state:
        await state.set_state(BookingFlow.name)
        await message.answer("Как вас зовут?", reply_markup=booking_navigation_keyboard())
    else:
        await state.set_state(BookingFlow.phone)
        await message.answer(
            "Отправьте номер кнопкой ниже или введите вручную:",
            reply_markup=booking_navigation_keyboard(request_contact=True),
        )


@router.message(BookingFlow.name)
async def capture_client_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 255:
        await message.answer("Введите имя длиной от 1 до 255 символов.")
        return
    await state.update_data(client_name=name)
    await state.set_state(BookingFlow.phone)
    await message.answer(
        "Отправьте номер кнопкой ниже или введите вручную:",
        reply_markup=booking_navigation_keyboard(request_contact=True),
    )


@router.message(BookingFlow.phone)
async def capture_client_phone(message: Message, state: FSMContext) -> None:
    if message.contact is not None:
        if (
            message.contact.user_id is not None
            and message.from_user is not None
            and message.contact.user_id != message.from_user.id
        ):
            await message.answer("Пожалуйста, отправьте именно свой контакт или введите номер.")
            return
        raw_phone = message.contact.phone_number
    else:
        raw_phone = message.text or ""
    try:
        phone = normalize_phone(raw_phone)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(phone=phone)
    await state.set_state(BookingFlow.comment)
    await message.answer(
        "Добавьте комментарий к записи или отправьте «-», если комментария нет:",
        reply_markup=booking_navigation_keyboard(),
    )


@router.message(BookingFlow.comment)
async def capture_client_comment(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    if message.from_user is None:
        return
    raw_comment = (message.text or "").strip()
    comment = None if raw_comment == "-" else raw_comment
    if comment is not None and len(comment) > 2000:
        await message.answer("Комментарий не должен превышать 2000 символов.")
        return
    await state.update_data(client_comment=comment)
    policy = await booking_service.get_reference_media_policy(
        actor_from_telegram(message.from_user)
    )
    await state.update_data(reference_media=[])
    await state.set_state(BookingFlow.references)
    await message.answer(
        "При желании прикрепите фотографии желаемого дизайна.\n\n"
        "Можно отправить несколько фотографий по одной или одним альбомом. "
        "Когда закончите, нажмите «Готово».\n\n"
        f"Максимальное количество: {policy.max_media}.",
        reply_markup=reference_media_keyboard(),
    )


@router.message(BookingFlow.references, F.photo)
async def capture_reference_photo(
    message: Message,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    if message.from_user is None or not message.photo:
        return
    policy = await booking_service.get_reference_media_policy(
        actor_from_telegram(message.from_user)
    )
    data = await state.get_data()
    media = [dict(item) for item in data.get("reference_media", []) if isinstance(item, dict)]
    photo = message.photo[-1]
    if any(item.get("telegram_file_unique_id") == photo.file_unique_id for item in media):
        if message.media_group_id is None:
            await message.answer("Эта фотография уже добавлена.")
        return
    if len(media) >= policy.max_media:
        await message.answer(
            f"Можно прикрепить не более {policy.max_media} фотографий. "
            "Лишняя фотография не добавлена.",
            reply_markup=reference_media_keyboard(),
        )
        return
    media.append(
        ReferenceMediaDraft(
            telegram_file_id=photo.file_id,
            telegram_file_unique_id=photo.file_unique_id,
        ).model_dump(mode="json")
    )
    await state.update_data(reference_media=media)
    log_event(
        logger,
        logging.INFO,
        "booking_reference_added",
        reference_count=len(media),
        is_album=message.media_group_id is not None,
    )
    if message.media_group_id is None:
        await message.answer(
            f"Фотография добавлена. Сейчас: {len(media)} из {policy.max_media}.",
            reply_markup=reference_media_keyboard(),
        )


@router.message(BookingFlow.references)
async def reject_non_photo_reference(message: Message) -> None:
    await message.answer(
        "Отправьте фотографию или воспользуйтесь кнопками ниже.",
        reply_markup=reference_media_keyboard(),
    )


@router.callback_query(BookingFlow.references, BookingReferenceCallback.filter())
async def handle_reference_action(
    callback: CallbackQuery,
    callback_data: BookingReferenceCallback,
    state: FSMContext,
    booking_service: BookingService,
    menu_service: MenuService,
) -> None:
    if callback_data.action == "cancel":
        await cancel_booking_callback(callback, state)
        return
    if callback_data.action == "back":
        await state.update_data(reference_media=[])
        await state.set_state(BookingFlow.comment)
        if isinstance(callback.message, Message):
            await callback.message.edit_text("Фотографии удалены из черновика.")
            await callback.message.answer(
                "Добавьте комментарий к записи или отправьте «-», если комментария нет:",
                reply_markup=booking_navigation_keyboard(),
            )
        await callback.answer()
        return
    if callback_data.action == "clear":
        await state.update_data(reference_media=[])
        log_event(logger, logging.INFO, "booking_reference_removed", removed_all=True)
        await callback.answer("Все фотографии удалены.")
        return
    if callback_data.action == "skip":
        await state.update_data(reference_media=[])
    elif callback_data.action != "done":
        await callback.answer("Эта кнопка устарела.", show_alert=True)
        return
    await _show_booking_confirmation(callback, state, booking_service, menu_service)


async def _show_booking_confirmation(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
    menu_service: MenuService,
) -> None:
    data = await state.get_data()
    try:
        service_id = int(str(data["service_id"]))
        window_id = int(data["window_id"])
        local_date = date.fromisoformat(str(data["local_date"]))
        staff_member_id = (
            int(str(data["staff_member_id"])) if data.get("staff_member_id") is not None else None
        )
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
            staff_member_id=staff_member_id,
            local_date=local_date,
        )
        window = next(item for item in availability.windows if item.id == window_id)
        info = await booking_service.get_business_info(actor_from_telegram(callback.from_user))
        client_name = str(data["client_name"])
        reference_media = [
            ReferenceMediaDraft.model_validate(item) for item in data.get("reference_media", [])
        ]
    except (DomainError, ValidationError, KeyError, StopIteration, ValueError) as exc:
        await state.clear()
        await callback.answer(str(exc) or "Выбранное время уже недоступно.", show_alert=True)
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Главное меню:",
                reply_markup=client_main_keyboard(await menu_service.get_capabilities()),
            )
        return
    await state.set_state(BookingFlow.confirm)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_booking_confirmation(
                availability.service,
                window,
                info,
                client_name=client_name,
                design_title=(
                    str(data["design_title"]) if data.get("design_title") is not None else None
                ),
                reference_media_count=len(reference_media),
            ),
            reply_markup=confirmation_keyboard(),
        )
        await callback.message.answer("Проверьте данные:", reply_markup=ReplyKeyboardRemove())
    await callback.answer()


@router.callback_query(
    BookingFlow.confirm,
    BookingCallback.filter(F.action == "change"),
)
async def change_booking(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    services = await booking_service.list_active_services(actor_from_telegram(callback.from_user))
    await state.clear()
    await state.set_state(BookingFlow.service)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Выберите услугу заново:",
            reply_markup=services_keyboard(services),
        )
    await callback.answer()


@router.callback_query(
    BookingFlow.confirm,
    BookingCallback.filter(F.action == "confirm"),
)
async def confirm_booking(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
    authorization_service: AuthorizationService,
    bot: Bot,
    correlation_id: str,
    menu_service: MenuService,
) -> None:
    data = await state.get_data()
    try:
        checkout_idempotency_key = data.get("checkout_idempotency_key")
        reservation_token = data.get("reservation_token")
        if not isinstance(checkout_idempotency_key, str) or not isinstance(reservation_token, str):
            checkout_idempotency_key = f"tg-booking:{token_urlsafe(24)}"
            reservation_token = token_urlsafe(32)
            await state.update_data(
                checkout_idempotency_key=checkout_idempotency_key,
                reservation_token=reservation_token,
            )
        request = BookingRequest(
            service_id=data["service_id"],
            window_id=data["window_id"],
            staff_member_id=data.get("staff_member_id"),
            client_name=data["client_name"],
            phone=data["phone"],
            client_comment=data.get("client_comment"),
            design_reference_id=data.get("design_reference_id"),
            reference_media=[
                ReferenceMediaDraft.model_validate(item) for item in data.get("reference_media", [])
            ],
            checkout_idempotency_key=checkout_idempotency_key,
            reservation_token=reservation_token,
        )
        receipt = await booking_service.book(
            actor_from_telegram(callback.from_user),
            request,
            correlation_id=correlation_id,
        )
    except BookingConflictError as exc:
        await _show_dates_after_conflict(callback, state, booking_service, data, str(exc))
        return
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_booking_receipt(receipt),
            reply_markup=appointment_links_keyboard(
                receipt.map_url,
                receipt.master_telegram_url,
                payment_url=receipt.payment_confirmation_url,
                manual_payment_id=(
                    receipt.payment_id if receipt.payment_mode is PaymentMode.MANUAL else None
                ),
            ),
        )
        await callback.message.answer(
            "Главное меню:",
            reply_markup=client_main_keyboard(await menu_service.get_capabilities()),
        )
    await callback.answer(
        "Запись подтверждена."
        if receipt.appointment_status is AppointmentStatus.CONFIRMED
        else "Время зарезервировано. Завершите оплату."
    )

    admin_text = render_admin_new_booking(receipt)
    recipients = await authorization_service.list_active_staff(
        business_id=DEFAULT_BUSINESS_ID,
        roles=LEGACY_ADMIN_ROLES,
    )
    for recipient in recipients:
        admin_telegram_id = recipient.telegram_id
        try:
            await bot.send_message(admin_telegram_id, admin_text)
            if len(receipt.reference_media) == 1:
                await bot.send_photo(
                    admin_telegram_id,
                    receipt.reference_media[0].telegram_file_id,
                    caption=f"Референс к записи №{receipt.appointment_id}",
                )
            elif receipt.reference_media:
                await bot.send_media_group(
                    admin_telegram_id,
                    [
                        InputMediaPhoto(
                            media=item.telegram_file_id,
                            caption=(
                                f"Референсы к записи №{receipt.appointment_id}"
                                if item.position == 0
                                else None
                            ),
                        )
                        for item in receipt.reference_media
                    ],
                )
        except TelegramAPIError:
            log_event(
                logger,
                logging.WARNING,
                "booking.admin_notification_failed",
                appointment_id=receipt.appointment_id,
            )


async def _show_dates_after_conflict(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
    data: dict[str, object],
    message: str,
) -> None:
    try:
        service_id = int(str(data["service_id"]))
        staff_member_id = (
            int(str(data["staff_member_id"])) if data.get("staff_member_id") is not None else None
        )
        availability = await booking_service.list_availability(
            actor_from_telegram(callback.from_user),
            service_id,
            staff_member_id=staff_member_id,
        )
    except (DomainError, KeyError, ValueError):
        await state.clear()
        await callback.answer(message, show_alert=True)
        return
    await state.set_state(BookingFlow.date)
    if isinstance(callback.message, Message):
        back_action = "back_masters" if data.get("master_selection_shown") else "back_services"
        await callback.message.edit_text(
            message + "\n\nВыберите другую дату:",
            reply_markup=dates_keyboard(
                available_dates(availability.windows),
                back_action=back_action,
            ),
        )
    await callback.answer(message, show_alert=True)
