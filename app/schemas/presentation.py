"""White-label projections safe for Telegram presentation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.domain.enums import BusinessType


class BusinessPresentation(BaseModel):
    """Public business identity and support/legal links from persisted settings."""

    model_config = ConfigDict(frozen=True)

    business_id: int
    display_name: str
    business_type: BusinessType
    timezone: str
    currency: str
    address: str | None = None
    map_url: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    logo_telegram_file_id: str | None = None
    support_name: str | None = None
    support_url: str | None = None
    support_hours: str | None = None
    support_instructions: str | None = None
    privacy_policy_url: str | None = None
    terms_url: str | None = None


class PublicMasterPresentation(BaseModel):
    """Public, non-sensitive master card."""

    model_config = ConfigDict(frozen=True)

    staff_member_id: int
    display_name: str
    bio: str | None = None
    specialization: str | None = None
    telegram_photo_file_id: str | None = None
