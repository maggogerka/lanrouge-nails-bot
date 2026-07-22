"""Router assembly for the multi-step client booking flow."""

from aiogram import Router

from app.handlers.client.booking_browse import router as browse_router
from app.handlers.client.booking_details import router as details_router

router = Router(name="client.booking")
router.include_routers(details_router, browse_router)

__all__ = ["router"]
