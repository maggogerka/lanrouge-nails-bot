"""Review submission and moderation projections."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

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
