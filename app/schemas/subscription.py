"""Safe CRM subscription projections."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import SubscriptionProvider, SubscriptionStatus

_SAFE_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SubscriptionView(BaseModel):
    """Billing state visible to the business owner and access guard."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    business_id: Annotated[int, Field(gt=0)]
    plan_code: str
    provider: SubscriptionProvider
    status: SubscriptionStatus
    paid_until: datetime | None = None
    grace_ends_at: datetime | None = None
    next_payment_at: datetime | None = None
    blocking_reason_code: str | None = None
    feature_limits: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)

    @field_validator("plan_code", "blocking_reason_code")
    @classmethod
    def safe_codes(cls, value: str | None) -> str | None:
        if value is not None and _SAFE_CODE.fullmatch(value) is None:
            raise ValueError("subscription codes must be bounded machine identifiers")
        return value
