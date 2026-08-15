"""Validated portfolio commands and client-safe projections."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import MediaType, PortfolioDisplayMode, PortfolioStatus


class PortfolioMediaInput(BaseModel):
    telegram_file_id: Annotated[str, Field(min_length=1, max_length=512)]
    telegram_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: MediaType = MediaType.PHOTO


class PortfolioCreate(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(max_length=2000)] | None = None
    linked_service_id: Annotated[int, Field(gt=0)] | None = None
    design_price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    sort_order: Annotated[int, Field(ge=-100000, le=100000)] = 0
    media: Annotated[list[PortfolioMediaInput], Field(min_length=1, max_length=10)]
    tag_names: Annotated[list[str], Field(max_length=10)] = Field(default_factory=list)
    staff_member_id: Annotated[int, Field(gt=0)] = 1

    @field_validator("title", "description", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("tag_names")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        unique = list(dict.fromkeys(value.casefold() for value in normalized))
        if len(unique) != len(normalized):
            raise ValueError("tag names must be unique")
        if any(len(value) > 100 for value in normalized):
            raise ValueError("tag name is too long")
        return normalized


class PortfolioMediaView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_file_id: str
    telegram_file_unique_id: str
    media_type: MediaType
    position: int


class PortfolioTagView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    is_active: bool


class PortfolioItemView(BaseModel):
    id: int
    title: str
    description: str | None
    linked_service_id: int | None
    linked_service_name: str | None
    design_price: Decimal | None
    status: PortfolioStatus
    sort_order: int
    published_at: datetime | None
    media: list[PortfolioMediaView]
    tags: list[PortfolioTagView]
    staff_member_id: int = 1
    master_name: str | None = None


class PortfolioMasterView(BaseModel):
    staff_member_id: int
    display_name: str
    telegram_photo_file_id: str | None = None


class PortfolioPage(BaseModel):
    items: list[PortfolioItemView]
    total: int
    page: int
    page_size: int

    @model_validator(mode="after")
    def page_must_exist(self) -> PortfolioPage:
        pages = max(1, (self.total + self.page_size - 1) // self.page_size)
        if self.page > pages:
            raise ValueError("page does not exist")
        return self

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


class PortfolioDisplayConfig(BaseModel):
    mode: PortfolioDisplayMode
    external_url: str | None
    button_text: str


class PortfolioDisplayUpdate(BaseModel):
    mode: PortfolioDisplayMode | None = None
    external_url: Annotated[str, Field(max_length=2048)] | None = None
    button_text: Annotated[str, Field(min_length=1, max_length=100)] | None = None

    @field_validator("external_url")
    @classmethod
    def url_must_be_absolute_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("portfolio URL must be an absolute HTTPS URL")
        return normalized

    @field_validator("button_text", mode="before")
    @classmethod
    def normalize_button_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> PortfolioDisplayUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one portfolio display field is required")
        return self
