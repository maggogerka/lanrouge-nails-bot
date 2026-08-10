"""Client routers for onboarding, booking and implemented menu sections."""

from aiogram import Router

from app.filters import IsAnyFeatureEnabled, IsFeatureEnabled
from app.handlers.client.appointments import router as appointments_router
from app.handlers.client.booking import router as booking_router
from app.handlers.client.marketing import router as marketing_router
from app.handlers.client.master_profile import router as master_profile_router
from app.handlers.client.menu import router as menu_router
from app.handlers.client.notifications import router as notifications_router
from app.handlers.client.onboarding import router as onboarding_router
from app.handlers.client.portfolio import router as portfolio_router
from app.handlers.client.reminders import router as reminders_router
from app.handlers.client.repeat_booking import router as repeat_booking_router
from app.handlers.client.reviews import router as reviews_router
from app.handlers.client.waitlist import router as waitlist_router
from app.schemas.features import FeatureName

router = Router(name="client")


def _require_feature(target: Router, feature: FeatureName) -> None:
    target.message.filter(IsFeatureEnabled(feature))
    target.callback_query.filter(IsFeatureEnabled(feature))


_require_feature(booking_router, FeatureName.ONLINE_BOOKING)
_require_feature(portfolio_router, FeatureName.PORTFOLIO)
_require_feature(reviews_router, FeatureName.REVIEWS)
_require_feature(repeat_booking_router, FeatureName.REPEAT_BOOKING)
_require_feature(waitlist_router, FeatureName.WAITLIST)
_require_feature(reminders_router, FeatureName.REMINDERS)
notifications_router.message.filter(
    IsAnyFeatureEnabled(FeatureName.REMINDERS, FeatureName.REPEAT_BOOKING)
)
notifications_router.callback_query.filter(
    IsAnyFeatureEnabled(FeatureName.REMINDERS, FeatureName.REPEAT_BOOKING)
)
router.include_routers(
    onboarding_router,
    marketing_router,
    master_profile_router,
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
