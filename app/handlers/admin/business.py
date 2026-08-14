"""Owner-only white-label business setup and Bot API profile synchronization."""

from __future__ import annotations

from html import escape
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InputProfilePhotoStatic,
    Message,
)
from pydantic import ValidationError

from app.domain.enums import SubscriptionStatus
from app.domain.errors import DomainError, EntityNotFoundError
from app.keyboards.admin.business import (
    BusinessProfileCallback,
    BusinessSupportCallback,
    BusinessTimezoneCallback,
    WorkstationCallback,
    business_address_keyboard,
    business_profile_keyboard,
    business_support_keyboard,
    business_timezone_keyboard,
    workstation_details_keyboard,
    workstation_list_keyboard,
)
from app.keyboards.admin.main import ADMIN_BUSINESS_SETTINGS_TEXT
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.common.optional_input import is_optional_skip, optional_input_keyboard
from app.schemas.authorization import StaffContext
from app.schemas.business import BusinessAdminView, BusinessProfileUpdate
from app.schemas.public_links import PublicLink, public_links_from_mapping
from app.schemas.workstation import WorkstationCreate, WorkstationView
from app.services.business_service import BusinessAdministrationService
from app.services.subscription_service import SubscriptionService
from app.services.workstation_service import WorkstationService
from app.states.business import BusinessProfileStates, BusinessWorkstationStates

router = Router(name="admin.business")

_FIELD_BY_ACTION = {
    "name": "display_name",
    "description": "description",
    "short": "short_description",
    "phone": "contact_phone",
    "address": "address",
    "map_url": "map_url",
    "timezone": "timezone",
    "privacy": "privacy_policy_url",
    "terms": "terms_url",
}

_PROMPT_BY_ACTION = {
    "name": "Введите название бренда:",
    "description": (
        "Введите описание до 512 символов. После синхронизации оно появится "
        "в полном описании профиля бота. Отправьте «-», чтобы очистить:"
    ),
    "short": (
        "Введите короткое описание до 120 символов. После синхронизации оно появится "
        "в BIO профиля бота. Отправьте «-», чтобы очистить:"
    ),
    "phone": "Введите клиентский телефон или «-», чтобы очистить:",
    "address": "Введите адрес салона так, как его увидит клиент, или «-», чтобы очистить:",
    "map_url": (
        "Пришлите HTTPS-ссылку на точку в Яндекс Картах, 2ГИС или Google Maps. "
        "Отправьте «-», чтобы убрать кнопку карты:"
    ),
    "timezone": (
        "Введите техническое название часового пояса в формате Регион/Город, "
        "например Europe/Moscow или Asia/Yekaterinburg:"
    ),
    "privacy": "Введите HTTPS-ссылку на политику:",
    "terms": "Введите HTTPS-ссылку на оферту или «-», чтобы очистить:",
}
_OPTIONAL_ACTIONS = {
    "description",
    "short",
    "phone",
    "address",
    "map_url",
    "terms",
}


def _render(view: BusinessAdminView) -> str:
    completed = "да" if view.setup_completed_at is not None else "нет"
    return (
        f"<b>Настройки бизнеса</b>\n"
        f"Бренд: <b>{escape(view.display_name)}</b>\n"
        f"Часовой пояс: <code>{view.timezone}</code>\n"
        f"Телефон салона: {escape(view.contact_phone or 'не задан')}\n"
        f"Адрес: {escape(view.address or 'не задан')}\n"
        f"Карта: {escape(view.map_url or 'не задана')}\n"
        f"Описание профиля: {escape(view.description or 'не задано')}\n"
        f"Короткое описание профиля: {escape(view.short_description or 'не задано')}\n"
        f"Политика: {escape(view.privacy_policy_url or 'не задана')}\n"
        f"Оферта: {escape(view.terms_url or 'не задана')}\n"
        f"Источников поддержки: {len(view.social_links)}\n"
        f"Первичная настройка завершена: {completed}"
    )


@router.message(F.text == ADMIN_BUSINESS_SETTINGS_TEXT)
async def show_business_profile(
    message: Message,
    business_service: BusinessAdministrationService,
    subscription_service: SubscriptionService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get(staff_context)
    subscription_text = await _subscription_summary(subscription_service, staff_context.business_id)
    await message.answer(
        _render(view) + f"\n\n{subscription_text}",
        reply_markup=business_profile_keyboard(
            business_type=view.business_type,
            is_bootstrap_owner=staff_context.is_bootstrap_owner,
            is_bookable=staff_context.is_bookable,
        ),
    )


@router.callback_query(BusinessProfileCallback.filter(F.action == "subscription"))
async def show_subscription(
    callback: CallbackQuery,
    subscription_service: SubscriptionService,
    staff_context: StaffContext,
) -> None:
    text = await _subscription_summary(subscription_service, staff_context.business_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(BusinessProfileCallback.filter(F.action == "address_menu"))
async def show_address_settings(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get(staff_context)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "<b>Адрес и карта</b>\n"
            f"Адрес: {escape(view.address or 'не задан')}\n"
            f"Ссылка на карту: {escape(view.map_url or 'не задана')}\n\n"
            "Адрес показывается текстом, а ссылка открывается отдельной кнопкой.",
            reply_markup=business_address_keyboard(),
        )
    await callback.answer()


@router.callback_query(BusinessProfileCallback.filter(F.action == "timezone_menu"))
async def show_timezone_settings(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "<b>Часовой пояс</b>\nВыберите ближайший город. Все окна и уведомления "
            "будут показываться по этому местному времени.",
            reply_markup=business_timezone_keyboard(),
        )
    await callback.answer()


@router.callback_query(BusinessProfileCallback.filter(F.action == "workstations"))
@router.callback_query(WorkstationCallback.filter(F.action == "list"))
async def show_workstations(
    callback: CallbackQuery,
    workstation_service: WorkstationService,
    staff_context: StaffContext,
) -> None:
    items = await workstation_service.list_all(staff_context)
    text = (
        "<b>Рабочие места</b>\n\n"
        "Рабочее место — физический стол или кабинет. Одновременно может быть "
        "занято только одно окно на каждом месте. Отметьте услуги, которые можно "
        "выполнять на нём."
    )
    if not items:
        text += "\n\nРабочих мест пока нет. Создайте первое, затем назначьте ему услуги."
    if isinstance(callback.message, Message):
        await callback.message.answer(
            text,
            reply_markup=workstation_list_keyboard(items),
        )
    await callback.answer()


@router.callback_query(WorkstationCallback.filter(F.action == "create"))
async def begin_workstation_creation(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(BusinessWorkstationStates.waiting_name)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите понятное название рабочего места, например «Маникюрный стол 1» "
            "или «Кабинет педикюра»:"
        )
    await callback.answer()


@router.message(BusinessWorkstationStates.waiting_name)
async def save_workstation(
    message: Message,
    state: FSMContext,
    workstation_service: WorkstationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        item = await workstation_service.create(
            staff_context,
            WorkstationCreate(name=message.text or ""),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, ValueError) as exc:
        await message.answer(f"Не удалось создать рабочее место: {escape(str(exc))}")
        return
    await state.clear()
    await message.answer(
        _render_workstation(item),
        reply_markup=workstation_details_keyboard(item),
    )


@router.callback_query(WorkstationCallback.filter(F.action == "view"))
async def show_workstation(
    callback: CallbackQuery,
    callback_data: WorkstationCallback,
    workstation_service: WorkstationService,
    staff_context: StaffContext,
) -> None:
    try:
        item = await workstation_service.get(staff_context, callback_data.workstation_id)
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_workstation(item),
            reply_markup=workstation_details_keyboard(item),
        )
    await callback.answer()


@router.callback_query(WorkstationCallback.filter(F.action.in_({"service_on", "service_off"})))
async def toggle_workstation_service(
    callback: CallbackQuery,
    callback_data: WorkstationCallback,
    workstation_service: WorkstationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        item = await workstation_service.set_service_enabled(
            staff_context,
            callback_data.workstation_id,
            callback_data.service_id,
            enabled=callback_data.action == "service_on",
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_workstation(item),
            reply_markup=workstation_details_keyboard(item),
        )
    await callback.answer("Настройка услуг сохранена.")


@router.callback_query(WorkstationCallback.filter(F.action.in_({"archive", "restore"})))
async def toggle_workstation_status(
    callback: CallbackQuery,
    callback_data: WorkstationCallback,
    workstation_service: WorkstationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        item = await workstation_service.set_active(
            staff_context,
            callback_data.workstation_id,
            active=callback_data.action == "restore",
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_workstation(item),
            reply_markup=workstation_details_keyboard(item),
        )
    await callback.answer("Статус рабочего места обновлён.")


@router.callback_query(BusinessTimezoneCallback.filter())
async def save_timezone_choice(
    callback: CallbackQuery,
    callback_data: BusinessTimezoneCallback,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        view = await business_service.update(
            staff_context,
            BusinessProfileUpdate(timezone=callback_data.timezone),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(_render(view), reply_markup=business_profile_keyboard())
    await callback.answer("Часовой пояс сохранён.")


@router.callback_query(BusinessProfileCallback.filter(F.action == "support_sources"))
async def show_support_sources(
    callback: CallbackQuery,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get(staff_context)
    links = public_links_from_mapping(view.social_links)
    lines = [
        "<b>Источники поддержки</b>",
        "Добавьте до 5 кнопок для связи с салоном. Они появятся у клиента в разделе "
        "«Поддержка и контакты».",
    ]
    lines.extend(f"• {escape(link.label)} — {escape(link.url)}" for link in links)
    if not links:
        lines.append("\nПока не добавлено ни одного источника.")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "\n".join(lines),
            reply_markup=business_support_keyboard(links),
        )
    await callback.answer()


@router.callback_query(BusinessSupportCallback.filter(F.action == "add"))
async def begin_support_source(
    callback: CallbackQuery,
    state: FSMContext,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get(staff_context)
    if len(public_links_from_mapping(view.social_links)) >= 5:
        await callback.answer("Можно добавить не более 5 источников.", show_alert=True)
        return
    await state.set_state(BusinessProfileStates.waiting_support_label)
    await state.set_data({})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите короткое название кнопки, например «Telegram», «WhatsApp» или «VK»:",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(BusinessProfileStates.waiting_support_label)
async def save_support_label(message: Message, state: FSMContext) -> None:
    label = (message.text or "").strip()
    try:
        PublicLink(label=label, url="https://example.test")
    except ValidationError as exc:
        await message.answer(f"Не удалось сохранить название: {escape(str(exc))}")
        return
    await state.update_data(support_label=label)
    await state.set_state(BusinessProfileStates.waiting_support_url)
    await message.answer(
        "Теперь пришлите HTTPS-ссылку, которая откроется по кнопке:",
        reply_markup=cancel_keyboard(),
    )


@router.message(BusinessProfileStates.waiting_support_url)
async def save_support_url(
    message: Message,
    state: FSMContext,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    label = str(data.get("support_label", ""))
    try:
        new_link = PublicLink(label=label, url=message.text or "")
        current = await business_service.get(staff_context)
        links = dict(current.social_links)
        links[new_link.label] = new_link.url
        view = await business_service.update(
            staff_context,
            BusinessProfileUpdate(
                social_links=links,
                client_support_name=None,
                client_support_url=None,
            ),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, ValueError) as exc:
        await message.answer(f"Не удалось сохранить ссылку: {escape(str(exc))}")
        return
    await state.clear()
    public_links = public_links_from_mapping(view.social_links)
    await message.answer(
        "Источник поддержки добавлен.",
        reply_markup=business_support_keyboard(public_links),
    )


@router.callback_query(BusinessSupportCallback.filter(F.action == "delete"))
async def delete_support_source(
    callback: CallbackQuery,
    callback_data: BusinessSupportCallback,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    current = await business_service.get(staff_context)
    links = list(public_links_from_mapping(current.social_links))
    if callback_data.index < 0 or callback_data.index >= len(links):
        await callback.answer("Список уже изменился. Откройте его заново.", show_alert=True)
        return
    del links[callback_data.index]
    view = await business_service.update(
        staff_context,
        BusinessProfileUpdate(
            social_links={link.label: link.url for link in links},
            client_support_name=None,
            client_support_url=None,
        ),
        correlation_id=correlation_id,
    )
    updated = public_links_from_mapping(view.social_links)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=business_support_keyboard(updated))
    await callback.answer("Источник удалён.")


@router.callback_query(BusinessProfileCallback.filter(F.action == "logo"))
async def begin_logo_upload(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BusinessProfileStates.waiting_logo)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отправьте квадратное изображение логотипа.",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.callback_query(BusinessProfileCallback.filter(F.action == "sync_bot"))
async def sync_bot_profile(
    callback: CallbackQuery,
    bot: Bot,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
) -> None:
    view = await business_service.get(staff_context)
    logo_warning = False
    try:
        await bot.set_my_name(name=view.display_name[:64])
        await bot.set_my_description(description=(view.description or "")[:512])
        await bot.set_my_short_description(short_description=(view.short_description or "")[:120])
        await bot.set_my_commands(
            commands=[
                BotCommand(command="start", description="Открыть главное меню"),
                BotCommand(command="whoami", description="Показать мой Telegram ID"),
                BotCommand(command="delete_my_data", description="Запросить удаление данных"),
            ]
        )
    except TelegramAPIError:
        await callback.answer("Telegram не применил профиль. Повторите позже.", show_alert=True)
        return
    if view.logo_telegram_file_id:
        try:
            buffer = BytesIO()
            await bot.download(view.logo_telegram_file_id, destination=buffer)
            uploaded = BufferedInputFile(buffer.getvalue(), filename="business-profile.jpg")
            await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=uploaded))
        except (OSError, TelegramAPIError):
            logo_warning = True
    result = "Тексты и команды профиля бота синхронизированы."
    if logo_warning:
        result += " Логотип Telegram не применил — попробуйте загрузить его заново."
    elif view.logo_telegram_file_id:
        result += " Логотип тоже обновлён."
    await callback.answer(result, show_alert=True)


@router.callback_query(BusinessProfileCallback.filter(F.action.in_(set(_FIELD_BY_ACTION))))
async def begin_text_edit(
    callback: CallbackQuery,
    callback_data: BusinessProfileCallback,
    state: FSMContext,
) -> None:
    await state.set_state(BusinessProfileStates.waiting_value)
    await state.set_data({"business_action": callback_data.action})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _PROMPT_BY_ACTION[callback_data.action],
            reply_markup=(
                optional_input_keyboard()
                if callback_data.action in _OPTIONAL_ACTIONS
                else cancel_keyboard()
            ),
        )
    await callback.answer()


@router.message(BusinessProfileStates.waiting_value)
async def save_text_value(
    message: Message,
    state: FSMContext,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    action = str((await state.get_data()).get("business_action", ""))
    field = _FIELD_BY_ACTION.get(action)
    if field is None:
        await state.clear()
        return
    raw = (message.text or "").strip()
    value = None if is_optional_skip(raw) else raw
    try:
        update = BusinessProfileUpdate.model_validate({field: value})
        view = await business_service.update(
            staff_context,
            update,
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, ValueError) as exc:
        await message.answer(f"Не удалось сохранить: {escape(str(exc))}")
        return
    await state.clear()
    await message.answer(
        _render(view),
        reply_markup=business_profile_keyboard(
            business_type=view.business_type,
            is_bootstrap_owner=staff_context.is_bootstrap_owner,
            is_bookable=staff_context.is_bookable,
        ),
    )


@router.message(BusinessProfileStates.waiting_logo, F.photo)
async def save_logo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    business_service: BusinessAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    if not message.photo:
        return
    photo = message.photo[-1]
    view = await business_service.update(
        staff_context,
        BusinessProfileUpdate(logo_telegram_file_id=photo.file_id),
        correlation_id=correlation_id,
    )
    warning = ""
    try:
        buffer = BytesIO()
        await bot.download(photo, destination=buffer)
        uploaded = BufferedInputFile(buffer.getvalue(), filename="business-profile.jpg")
        await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=uploaded))
    except (OSError, TelegramAPIError):
        warning = "\nЛоготип сохранён для меню, но Telegram-профиль не обновился."
    await state.clear()
    await message.answer(
        _render(view) + warning,
        reply_markup=business_profile_keyboard(
            business_type=view.business_type,
            is_bootstrap_owner=staff_context.is_bootstrap_owner,
            is_bookable=staff_context.is_bookable,
        ),
    )


@router.message(BusinessProfileStates.waiting_logo)
async def reject_non_photo(message: Message) -> None:
    await message.answer("Нужно отправить изображение.")


async def _subscription_summary(
    subscription_service: SubscriptionService,
    business_id: int,
) -> str:
    try:
        subscription = await subscription_service.get_status(business_id)
    except EntityNotFoundError:
        return "<b>CRM-подписка</b>\nСтатус ещё не инициализирован. Обратитесь в поддержку."
    status_labels = {
        SubscriptionStatus.TRIAL: "пробный период",
        SubscriptionStatus.ACTIVE: "активна",
        SubscriptionStatus.PAST_DUE: "ожидает оплаты",
        SubscriptionStatus.SUSPENDED: "приостановлена",
        SubscriptionStatus.CANCELLED: "отменена",
    }
    rows = [
        "<b>CRM-подписка</b>",
        f"Тариф: <code>{escape(subscription.plan_code)}</code>",
        f"Статус: {status_labels[subscription.status]}",
    ]
    if subscription.paid_until is not None:
        rows.append(f"Оплачено до: {subscription.paid_until:%d.%m.%Y}")
    if subscription.grace_ends_at is not None:
        rows.append(f"Льготный период до: {subscription.grace_ends_at:%d.%m.%Y}")
    if subscription.next_payment_at is not None:
        rows.append(f"Следующее списание: {subscription.next_payment_at:%d.%m.%Y}")
    if SubscriptionService.owner_warning_due(subscription):
        rows.append("⚠️ Срок подписки заканчивается или требуется оплата.")
    rows.append("Оплата услуг клиентами учитывается отдельно от CRM-подписки.")
    return "\n".join(rows)


def _render_workstation(item: WorkstationView) -> str:
    enabled = [service.service_name for service in item.services if service.enabled]
    services = ", ".join(escape(name) for name in enabled) or "не выбраны"
    status = "активно" if item.is_active else "в архиве"
    return (
        f"<b>🪑 {escape(item.name)}</b>\n"
        f"Статус: {status}\n"
        f"Доступные услуги: {services}\n\n"
        "Нажмите на услугу ниже, чтобы разрешить или запретить её на этом месте."
    )
