"""Pure validation and first/last-touch attribution projection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.domain.errors import DomainError

_CAMPAIGN_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class CampaignValidationError(DomainError):
    """A Telegram start payload is not a safe campaign identifier."""


def validate_campaign_code(raw_code: str) -> str:
    """Normalize a Telegram deep-link code and reject free-form/PII-shaped payloads."""

    code = raw_code.strip().lower()
    if len(code.encode("ascii", errors="ignore")) != len(code):
        raise CampaignValidationError("campaign code must contain ASCII characters only")
    if _CAMPAIGN_CODE_PATTERN.fullmatch(code) is None:
        raise CampaignValidationError(
            "campaign code must contain 1..64 lowercase letters, digits, '_' or '-'"
        )
    return code


@dataclass(frozen=True, slots=True)
class AttributionProjection:
    """First touch remains immutable while every valid later touch advances last touch."""

    first_source_id: int
    first_touched_at: datetime
    last_source_id: int
    last_touched_at: datetime
    touch_count: int

    def __post_init__(self) -> None:
        if self.first_source_id <= 0 or self.last_source_id <= 0:
            raise CampaignValidationError("source IDs must be positive")
        if self.touch_count < 1:
            raise CampaignValidationError("touch count must be positive")
        if self.first_touched_at.tzinfo is None or self.first_touched_at.utcoffset() is None:
            raise CampaignValidationError("first touch timestamp must be timezone-aware")
        if self.last_touched_at.tzinfo is None or self.last_touched_at.utcoffset() is None:
            raise CampaignValidationError("last touch timestamp must be timezone-aware")
        if self.last_touched_at < self.first_touched_at:
            raise CampaignValidationError("last touch cannot precede first touch")

    @classmethod
    def first(cls, *, source_id: int, touched_at: datetime) -> AttributionProjection:
        return cls(
            first_source_id=source_id,
            first_touched_at=touched_at,
            last_source_id=source_id,
            last_touched_at=touched_at,
            touch_count=1,
        )

    def touch(self, *, source_id: int, touched_at: datetime) -> AttributionProjection:
        if touched_at < self.last_touched_at:
            raise CampaignValidationError("an attribution touch cannot move backwards in time")
        return AttributionProjection(
            first_source_id=self.first_source_id,
            first_touched_at=self.first_touched_at,
            last_source_id=source_id,
            last_touched_at=touched_at,
            touch_count=self.touch_count + 1,
        )
