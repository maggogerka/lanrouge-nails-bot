"""Client routers for onboarding, booking and implemented menu sections."""

from aiogram import Router

from app.handlers.client.appointments import router as appointments_router
from app.handlers.client.booking import router as booking_router
from app.handlers.client.marketing import router as marketing_router
from app.handlers.client.menu import router as menu_router
from app.handlers.client.notifications import router as notifications_router
from app.handlers.client.onboarding import router as onboarding_router
from app.handlers.client.portfolio import router as portfolio_router
from app.handlers.client.reminders import router as reminders_router
from app.handlers.client.repeat_booking import router as repeat_booking_router
from app.handlers.client.reviews import router as reviews_router
from app.handlers.client.waitlist import router as waitlist_router

router = Router(name="client")
router.include_routers(
    onboarding_router,
    marketing_router,
    reminders_router,
    reviews_router,
    repeat_booking_router,
    notifications_router,
    appointments_router,
    portfolio_router,
    waitlist_router,
    booking_router,
    menu_router,
)

__all__ = ["router"]
