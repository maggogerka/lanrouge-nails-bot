"""Feature visibility snapshot used to build Telegram reply menus."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MenuCapabilities:
    portfolio_visible: bool
    reviews_visible: bool
    master_profile_visible: bool
