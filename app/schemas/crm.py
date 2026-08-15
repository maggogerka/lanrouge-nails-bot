"""Administrator-only CRM commands and safe client-card projections."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import AppointmentStatus


class ClientTagCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    marker: Annotated[str, Field(max_length=32)] | None = None

    @field_validator("name", "marker", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


class ClientTagView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    marker: str | None
    is_active: bool


class ClientNoteCreate(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=2000)]

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ClientNoteView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    author_id: int
    text: str
    created_at: datetime
    archived_at: datetime | None


class ClientSummaryView(BaseModel):
    id: int
    telegram_id: int
    display_name: str
    username: str | None
    telegram_profile_url: str | None = None
    masked_phone: str | None
    marketing_subscribed: bool
    is_blocked: bool
    is_self_booking_blocked: bool


class ClientAppointmentView(BaseModel):
    id: int
    status: AppointmentStatus
    service_name: str
    price: Decimal
    start_at: datetime
    end_at: datetime


class ClientCardView(ClientSummaryView):
    phone: str | None
    completed_visits: int
    cancellations: int
    no_shows: int
    appointments_total: int
    appointments: list[ClientAppointmentView]
    tags: list[ClientTagView]
    notes: list[ClientNoteView]


class ClientPage(BaseModel):
    items: list[ClientSummaryView]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


def safe_telegram_profile_url(username: str | None) -> str | None:
    """Build a public profile URL only for a currently valid Telegram username."""

    if username is None or re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) is None:
        return None
    return f"https://t.me/{username}"
