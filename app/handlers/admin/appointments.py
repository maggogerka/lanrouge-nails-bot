"""Router assembly for administrative appointment workflows."""

from aiogram import Router

from app.handlers.admin.appointment_browse import router as browse_router
from app.handlers.admin.appointment_reschedule import router as reschedule_router

router = Router(name="admin.appointments")
router.include_routers(browse_router, reschedule_router)

__all__ = ["router"]
