"""Aggregate small service catalog routers."""

from aiogram import Router

from app.handlers.admin.service_browse import router as browse_router
from app.handlers.admin.service_create import router as create_router
from app.handlers.admin.service_edit import router as edit_router

router = Router(name="admin.services")
router.include_routers(browse_router, create_router, edit_router)

__all__ = ["router"]
