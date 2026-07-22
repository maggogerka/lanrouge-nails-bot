"""Review submission and moderation projections."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.enums import ReviewModerationStatus


class ReviewCreate(BaseModel):
    rating: Annotated[int, Field(ge=1, le=5)]
    text: Annotated[str, Field(max_length=2000)] | None = None
    publication_consent: bool = False

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class ReviewView(BaseModel):
    id: int
    appointment_id: int
    client_id: int
    client_name: str
    rating: int
    text: str | None
    publication_consent: bool
    moderation_status: ReviewModerationStatus
    published_at: datetime | None
    created_at: datetime
    edited_at: datetime | None = None
    is_admin_edited: bool = False
    deleted_at: datetime | None = None
    deletion_reason: str | None = None


class ReviewAdminUpdate(BaseModel):
    rating: Annotated[int, Field(ge=1, le=5)] | None = None
    text: Annotated[str, Field(max_length=2000)] | None = None
    moderation_status: ReviewModerationStatus | None = None

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one review field must be supplied")
        return self
