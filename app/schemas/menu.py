"""Feature visibility snapshot used to build Telegram reply menus."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MenuCapabilities:
    online_booking_visible: bool = True
    appointments_visible: bool = True
    services_visible: bool = True
    masters_visible: bool = True
    portfolio_visible: bool = True
    portfolio_management_visible: bool = True
    reviews_visible: bool = True
    notifications_visible: bool = True
    payments_visible: bool = True
    waitlist_visible: bool = True
    support_visible: bool = True
    privacy_visible: bool = True
    broadcasts_visible: bool = True
    master_profile_visible: bool = True
