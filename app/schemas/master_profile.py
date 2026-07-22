"""Validated commands and projections for the public master profile."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_https_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must be an absolute HTTPS URL without credentials")
    return normalized


class MasterPublicLinkInput(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=100)]
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    sort_order: Annotated[int, Field(ge=-100000, le=100000)] = 0
    is_active: bool = True

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_https_url(value) or ""


class MasterPublicLinkView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    url: str
    sort_order: int
    is_active: bool


class MasterProfileUpdate(BaseModel):
    display_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    bio: Annotated[str, Field(max_length=4000)] | None = None
    telegram_photo_file_id: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    telegram_photo_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    address: Annotated[str, Field(max_length=500)] | None = None
    map_url: Annotated[str, Field(max_length=2048)] | None = None
    telegram_url: Annotated[str, Field(max_length=2048)] | None = None

    @field_validator("display_name", "bio", "address", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("map_url", "telegram_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _validate_https_url(value)

    @model_validator(mode="after")
    def require_change(self) -> MasterProfileUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one profile field is required")
        if "display_name" in self.model_fields_set and self.display_name is None:
            raise ValueError("display_name cannot be empty")
        photo_fields = {
            "telegram_photo_file_id",
            "telegram_photo_file_unique_id",
        } & self.model_fields_set
        if photo_fields and len(photo_fields) != 2:
            raise ValueError("both Telegram photo identifiers must be changed together")
        return self


class MasterProfileView(BaseModel):
    id: int
    display_name: str
    bio: str | None
    telegram_photo_file_id: str | None
    telegram_photo_file_unique_id: str | None
    address: str | None
    map_url: str | None
    telegram_url: str | None
    is_published: bool
    links: list[MasterPublicLinkView]
