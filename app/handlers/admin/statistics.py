"""Owner/manager acquisition funnel and campaign link administration."""

from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.acquisition import CampaignValidationError, validate_campaign_code
from app.domain.errors import DomainError
from app.keyboards.admin.main import ADMIN_STATISTICS_TEXT
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.admin.statistics import AcquisitionCallback, acquisition_statistics_keyboard
from app.schemas.authorization import StaffContext
from app.services.acquisition_admin_service import AcquisitionAdministrationService
from app.states.acquisition import AcquisitionStates

router = Router(name="admin.statistics")


async def _show_statistics(
    message: Message,
    service: AcquisitionAdministrationService,
    actor: StaffContext,
) -> None:
    items = await service.list_metrics(actor)
    lines = ["<b>Источники клиентов</b>"]
    if not items:
        lines.append("Активных источников пока нет.")
    for item in items:
        lines.append(
            f"\n<b>{escape(item.source.display_name)}</b> · <code>{item.source.code}</code>\n"
            f"Пришли: {item.clients_arrived} · начали запись: {item.clients_started_booking}\n"
            f"Завершили визит: {item.clients_completed_booking} · повторные: {item.repeat_clients}"
        )
    await message.answer(
        "\n".join(lines),
        reply_markup=acquisition_statistics_keyboard(items, actor_role=actor.role),
    )


@router.message(F.text == ADMIN_STATISTICS_TEXT)
async def show_statistics(
    message: Message,
    acquisition_admin_service: AcquisitionAdministrationService,
    staff_context: StaffContext,
) -> None:
    await _show_statistics(message, acquisition_admin_service, staff_context)


@router.callback_query(F.data == "acq:new")
async def begin_source_creation(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AcquisitionStates.waiting_code)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите короткий код источника: латинские буквы, цифры, дефис или подчёркивание. "
            "Не добавляйте имя, телефон или другие персональные данные.",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(AcquisitionStates.waiting_code)
async def accept_source_code(message: Message, state: FSMContext) -> None:
    try:
        code = validate_campaign_code(message.text or "")
    except CampaignValidationError:
        await message.answer("Код должен содержать 1–64 латинских символа, цифры, _ или -.")
        return
    await state.update_data(acquisition_code=code)
    await state.set_state(AcquisitionStates.waiting_name)
    await message.answer(
        "Введите понятное название источника, например «Реклама у блогера».",
        reply_markup=cancel_keyboard(),
    )


@router.message(AcquisitionStates.waiting_name)
async def create_source(
    message: Message,
    state: FSMContext,
    acquisition_admin_service: AcquisitionAdministrationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    try:
        await acquisition_admin_service.create_source(
            staff_context,
            code=str(data.get("acquisition_code", "")),
            display_name=message.text or "",
            channel="custom",
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError, ValueError) as exc:
        await message.answer(f"Не удалось создать источник: {escape(str(exc))}")
        return
    await state.clear()
    await message.answer("Источник создан. Откройте «📊 Статистика», чтобы получить ссылку.")


@router.callback_query(AcquisitionCallback.filter())
async def show_source_link(
    callback: CallbackQuery,
    callback_data: AcquisitionCallback,
    bot: Bot,
    acquisition_admin_service: AcquisitionAdministrationService,
    staff_context: StaffContext,
) -> None:
    items = await acquisition_admin_service.list_metrics(staff_context)
    source = next(
        (item.source for item in items if item.source.id == callback_data.source_id),
        None,
    )
    if source is None:
        await callback.answer("Источник недоступен или удалён.", show_alert=True)
        return
    bot_user = await bot.get_me()
    if not bot_user.username:
        await callback.answer("Сначала задайте username бота через BotFather.", show_alert=True)
        return
    link = acquisition_admin_service.link_for(source, bot_user.username)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"<b>{escape(source.display_name)}</b>\n"
            f"Ссылка: <code>{escape(link.deep_link)}</code>\n\n"
            "Эта же ссылка является безопасным содержимым QR-кода. "
            "QR можно создать в Mini App или утверждённом генераторе "
            "без добавления персональных данных."
        )
    await callback.answer()
