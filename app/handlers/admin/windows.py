"""Aggregate small availability-window routers."""

from aiogram import Router

from app.handlers.admin.window_browse import router as browse_router
from app.handlers.admin.window_create import router as create_router

router = Router(name="admin.windows")
router.include_routers(browse_router, create_router)

__all__ = ["router"]
