"""Owner-editable white-label business profile DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import BusinessStatus, BusinessType
from app.schemas.public_links import normalize_public_link_mapping


class BusinessAdminView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: Annotated[int, Field(gt=0)]
    slug: str
    display_name: str
    description: str | None
    short_description: str | None
    business_type: BusinessType
    status: BusinessStatus
    timezone: str
    currency: str
    address: str | None
    map_url: str | None
    contact_phone: str | None
    logo_telegram_file_id: str | None
    client_support_name: str | None
    client_support_url: str | None
    client_support_hours: str | None
    client_support_instructions: str | None
    social_links: dict[str, str] = Field(default_factory=dict)
    privacy_policy_url: str | None
    privacy_policy_version: str | None
    terms_url: str | None
    terms_version: str | None
    setup_completed_at: datetime | None


class BusinessWelcomeView(BaseModel):
    """Draft and published welcome projections without Telegram user data."""

    model_config = ConfigDict(frozen=True)

    draft_text: str
    draft_photo_file_id: str | None
    published_text: str
    published_photo_file_id: str | None
    published_at: datetime | None


class BusinessProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    description: Annotated[str, Field(max_length=512)] | None = None
    short_description: Annotated[str, Field(max_length=120)] | None = None
    business_type: BusinessType | None = None
    timezone: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    address: Annotated[str, Field(max_length=500)] | None = None
    map_url: Annotated[str, Field(max_length=2048)] | None = None
    contact_phone: Annotated[str, Field(max_length=32)] | None = None
    logo_telegram_file_id: Annotated[str, Field(max_length=512)] | None = None
    client_support_name: Annotated[str, Field(max_length=100)] | None = None
    client_support_url: Annotated[str, Field(max_length=2048)] | None = None
    client_support_hours: Annotated[str, Field(max_length=255)] | None = None
    client_support_instructions: Annotated[str, Field(max_length=2000)] | None = None
    privacy_policy_url: Annotated[str, Field(max_length=2048)] | None = None
    privacy_policy_version: Annotated[str, Field(max_length=64)] | None = None
    terms_url: Annotated[str, Field(max_length=2048)] | None = None
    terms_version: Annotated[str, Field(max_length=64)] | None = None
    social_links: dict[str, str] | None = None

    @field_validator("display_name", mode="before")
    @classmethod
    def required_text_is_trimmed(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "description",
        "short_description",
        "address",
        "contact_phone",
        "client_support_name",
        "client_support_hours",
        "client_support_instructions",
        "privacy_policy_version",
        "terms_version",
        mode="before",
    )
    @classmethod
    def optional_text_is_trimmed(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("map_url", "client_support_url", "privacy_policy_url", "terms_url")
    @classmethod
    def public_urls_use_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("public URL must use HTTPS without embedded credentials")
        return value

    @field_validator("social_links", mode="before")
    @classmethod
    def support_links_are_safe(cls, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        return normalize_public_link_mapping(value)

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA name") from exc
        return value

    @model_validator(mode="after")
    def at_least_one_field(self) -> BusinessProfileUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one business field must be supplied")
        for field in ("display_name", "business_type", "timezone", "social_links"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must not be null")
        return self
