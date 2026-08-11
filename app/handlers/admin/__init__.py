"""Administrative routers protected at the parent boundary."""

from aiogram import Router

from app.filters import HasStaffPermission, IsFeatureEnabled, IsStaff
from app.handlers.admin.appointments import router as appointments_router
from app.handlers.admin.broadcasts import router as broadcasts_router
from app.handlers.admin.business import router as business_router
from app.handlers.admin.crm import router as crm_router
from app.handlers.admin.features import router as features_router
from app.handlers.admin.master_profile import router as master_profile_router
from app.handlers.admin.menu import router as menu_router
from app.handlers.admin.payments import router as payments_router
from app.handlers.admin.portfolio import router as portfolio_router
from app.handlers.admin.privacy import router as privacy_router
from app.handlers.admin.reviews import router as reviews_router
from app.handlers.admin.services import router as services_router
from app.handlers.admin.settings import router as settings_router
from app.handlers.admin.staff import router as staff_router
from app.handlers.admin.statistics import router as statistics_router
from app.handlers.admin.waitlist import router as waitlist_router
from app.handlers.admin.welcome import router as welcome_router
from app.handlers.admin.windows import router as windows_router
from app.middlewares.staff_context import StaffContextMiddleware
from app.schemas.authorization import StaffPermission
from app.schemas.features import FeatureName
from app.security import LEGACY_ADMIN_ROLES

router = Router(name="admin")
router.message.filter(IsStaff(allowed_roles=LEGACY_ADMIN_ROLES))
router.callback_query.filter(IsStaff(allowed_roles=LEGACY_ADMIN_ROLES))
router.message.middleware(StaffContextMiddleware())
router.callback_query.middleware(StaffContextMiddleware())


def _require_permission(target: Router, permission: StaffPermission) -> None:
    target.message.filter(HasStaffPermission(permission))
    target.callback_query.filter(HasStaffPermission(permission))


def _require_feature(target: Router, feature: FeatureName) -> None:
    target.message.filter(IsFeatureEnabled(feature))
    target.callback_query.filter(IsFeatureEnabled(feature))


_require_permission(appointments_router, StaffPermission.MANAGE_ALL_APPOINTMENTS)
_require_permission(broadcasts_router, StaffPermission.MANAGE_BROADCASTS)
_require_permission(business_router, StaffPermission.MANAGE_BUSINESS)
_require_permission(crm_router, StaffPermission.MANAGE_ALL_CLIENTS)
_require_permission(features_router, StaffPermission.VIEW_FEATURE_FLAGS)
_require_permission(master_profile_router, StaffPermission.MANAGE_BUSINESS)
_require_permission(payments_router, StaffPermission.VIEW_PAYMENTS)
_require_permission(portfolio_router, StaffPermission.MANAGE_SERVICES)
_require_permission(privacy_router, StaffPermission.HANDLE_DATA_DELETION)
_require_permission(reviews_router, StaffPermission.MANAGE_ALL_CLIENTS)
_require_permission(services_router, StaffPermission.MANAGE_SERVICES)
_require_permission(settings_router, StaffPermission.MANAGE_PRIVATE_SETTINGS)
_require_permission(staff_router, StaffPermission.VIEW_STAFF)
_require_permission(statistics_router, StaffPermission.VIEW_ALL_STATISTICS)
_require_permission(waitlist_router, StaffPermission.MANAGE_ALL_APPOINTMENTS)
_require_permission(windows_router, StaffPermission.MANAGE_ALL_SCHEDULES)
_require_permission(welcome_router, StaffPermission.MANAGE_BUSINESS)

_require_feature(broadcasts_router, FeatureName.BROADCASTS)
_require_feature(portfolio_router, FeatureName.PORTFOLIO)
_require_feature(reviews_router, FeatureName.REVIEWS)
_require_feature(waitlist_router, FeatureName.WAITLIST)
router.include_routers(
    menu_router,
    master_profile_router,
    appointments_router,
    broadcasts_router,
    business_router,
    welcome_router,
    crm_router,
    features_router,
    services_router,
    portfolio_router,
    payments_router,
    privacy_router,
    reviews_router,
    waitlist_router,
    windows_router,
    settings_router,
    staff_router,
    statistics_router,
)

__all__ = ["router"]
