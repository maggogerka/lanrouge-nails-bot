"""Validated DTOs for consent, availability and client booking."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.booking import normalize_phone
from app.schemas.service import ServiceView


class ClientActor(BaseModel):
    """Non-sensitive Telegram identity copied into application use cases."""

    telegram_id: Annotated[int, Field(gt=0)]
    username: Annotated[str, Field(max_length=64)] | None = None
    first_name: Annotated[str, Field(max_length=255)] | None = None
    last_name: Annotated[str, Field(max_length=255)] | None = None


class ConsentStatus(BaseModel):
    """Current consent state used to choose the onboarding screen."""

    privacy_accepted: bool
    marketing_answered: bool
    marketing_accepted: bool


class BusinessInfo(BaseModel):
    """Public studio contacts loaded from business settings."""

    business_name: str
    address: str
    map_url: str
    master_telegram_url: str


class BookingWindowView(BaseModel):
    """Client-safe availability projection that excludes admin comments."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    start_at: datetime
    end_at: datetime
    timezone: str


class BookingAvailability(BaseModel):
    """One service and all currently selectable matching windows."""

    service: ServiceView
    timezone: str
    windows: list[BookingWindowView]


class BookingRequest(BaseModel):
    """Final client-entered values submitted for transactional revalidation."""

    service_id: Annotated[int, Field(gt=0)]
    window_id: Annotated[int, Field(gt=0)]
    client_name: Annotated[str, Field(min_length=1, max_length=255)]
    phone: Annotated[str, Field(max_length=32)]
    client_comment: Annotated[str, Field(max_length=2000)] | None = None

    @field_validator("client_name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone")
    @classmethod
    def normalize_declared_phone(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("client_comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


class BookingReceipt(BaseModel):
    """Committed booking details safe to render to the client or administrator."""

    appointment_id: int
    service_name: str
    price: Decimal
    duration_min_minutes: int
    duration_max_minutes: int
    start_at: datetime
    end_at: datetime
    timezone: str
    address: str
    map_url: str
    master_telegram_url: str
    client_name: str
    phone: str
