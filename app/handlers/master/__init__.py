"""Master-only routers isolated from legacy administrative CRUD."""

from aiogram import Router

from app.domain.enums import StaffRole
from app.filters import IsStaff
from app.handlers.master.menu import router as menu_router
from app.middlewares.staff_context import StaffContextMiddleware

router = Router(name="master")
router.message.filter(IsStaff(allowed_roles={StaffRole.MASTER}))
router.callback_query.filter(IsStaff(allowed_roles={StaffRole.MASTER}))
router.message.middleware(StaffContextMiddleware())
router.callback_query.middleware(StaffContextMiddleware())
router.include_router(menu_router)

__all__ = ["router"]
