"""Client routers for onboarding, booking and implemented menu sections."""

from aiogram import Router

from app.handlers.client.booking import router as booking_router
from app.handlers.client.menu import router as menu_router
from app.handlers.client.onboarding import router as onboarding_router

router = Router(name="client")
router.include_routers(onboarding_router, booking_router, menu_router)

__all__ = ["router"]
