"""Telegram routers assembled without business logic."""

from aiogram import Router

from app.handlers.common import router as common_router
from app.handlers.errors import handle_unexpected_error

root_router = Router(name="root")
root_router.errors.register(handle_unexpected_error)
root_router.include_router(common_router)

__all__ = ["root_router"]
