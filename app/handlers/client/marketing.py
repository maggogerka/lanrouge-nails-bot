"""Authorize, track and route internal broadcast buttons."""

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.enums import MarketingEventType
from app.domain.errors import DomainError
from app.handlers.client.booking_browse import start_booking
from app.handlers.client.common import actor_from_telegram
from app.handlers.client.portfolio import show_portfolio
from app.keyboards.client.consent import privacy_consent_keyboard
from app.keyboards.client.marketing import MarketingCallback
from app.services.booking_service import BookingService
from app.services.consent_service import ConsentService
from app.services.marketing_event_service import MarketingEventService
from app.services.portfolio_service import PortfolioService
from app.services.presentation_service import PresentationService
from app.states.booking import PENDING_MARKETING_BOOKING_KEY

router = Router(name="client.marketing")


@router.callback_query(MarketingCallback.filter(F.action.in_({"book", "available_windows"})))
async def marketing_booking_click(
    callback: CallbackQuery,
    callback_data: MarketingCallback,
    state: FSMContext,
    marketing_event_service: MarketingEventService,
    booking_service: BookingService,
    consent_service: ConsentService,
    presentation_service: PresentationService,
) -> None:
    event = (
        MarketingEventType.BOOKING_CLICKED
        if callback_data.action == "book"
        else MarketingEventType.WINDOWS_CLICKED
    )
    actor = actor_from_telegram(callback.from_user)
    status = await consent_service.get_or_create_status(actor)
    if not status.privacy_accepted:
        business = await presentation_service.get_business()
        if business.privacy_policy_url is None:
            await callback.answer(
                "Запись временно недоступна: политика конфиденциальности не опубликована.",
                show_alert=True,
            )
            return
        await state.update_data(
            {
                PENDING_MARKETING_BOOKING_KEY: {
                    "broadcast_id": callback_data.broadcast_id,
                    "event_type": event.value,
                }
            }
        )
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "Чтобы продолжить запись, подтвердите актуальную политику "
                "конфиденциальности. Настройка рекламной подписки при этом не меняется.",
                reply_markup=privacy_consent_keyboard(str(business.privacy_policy_url)),
            )
        await callback.answer()
        return
    try:
        await marketing_event_service.record(actor, callback_data.broadcast_id, event)
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await start_booking(callback.message, state, booking_service, actor=actor)
    await callback.answer()


@router.callback_query(MarketingCallback.filter(F.action == "portfolio"))
async def marketing_portfolio_click(
    callback: CallbackQuery,
    callback_data: MarketingCallback,
    marketing_event_service: MarketingEventService,
    portfolio_service: PortfolioService,
    bot: Bot,
) -> None:
    try:
        await marketing_event_service.record(
            actor_from_telegram(callback.from_user),
            callback_data.broadcast_id,
            MarketingEventType.PORTFOLIO_CLICKED,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await show_portfolio(callback.message, portfolio_service, bot)
    await callback.answer()
