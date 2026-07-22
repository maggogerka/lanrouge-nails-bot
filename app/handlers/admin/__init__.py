"""Administrative routers protected at the parent boundary."""

from aiogram import Router

from app.filters import IsAdmin
from app.handlers.admin.menu import router as menu_router
from app.handlers.admin.services import router as services_router

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())
router.include_routers(menu_router, services_router)

__all__ = ["router"]
