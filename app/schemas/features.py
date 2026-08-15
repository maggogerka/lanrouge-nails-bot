"""Typed feature-flag snapshot shared by handlers, services, and workers."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class FeatureName(StrEnum):
    ONLINE_BOOKING = "online_booking"
    MASTER_SELECTION = "master_selection"
    WAITLIST = "waitlist"
    PORTFOLIO = "portfolio"
    REVIEWS = "reviews"
    REFERENCE_PHOTOS = "reference_photos"
    REMINDERS = "reminders"
    REPEAT_BOOKING = "repeat_booking"
    BROADCASTS = "broadcasts"
    LOYALTY = "loyalty"
    STATISTICS = "statistics"
    PREPAYMENT = "prepayment"
    MANUAL_PAYMENTS = "manual_payments"
    YOOKASSA_PAYMENTS = "yookassa_payments"
    MINI_APP = "mini_app"
    CLIENT_SUPPORT = "client_support"


class FeatureSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    online_booking: bool
    master_selection: bool
    waitlist: bool
    portfolio: bool
    reviews: bool
    reference_photos: bool
    reminders: bool
    repeat_booking: bool
    broadcasts: bool
    loyalty: bool
    statistics: bool
    prepayment: bool
    manual_payments: bool
    yookassa_payments: bool
    mini_app: bool
    client_support: bool

    def enabled(self, feature: FeatureName) -> bool:
        return bool(getattr(self, feature.value))
