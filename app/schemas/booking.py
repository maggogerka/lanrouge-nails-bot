"""Validated DTOs for consent, availability and client booking."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from secrets import token_urlsafe
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.domain.booking import normalize_phone
from app.domain.enums import AppointmentStatus, MediaType, PaymentMode, PaymentStatus
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


class NotificationPreferences(BaseModel):
    """Client-visible notification choices; service messages cannot be disabled."""

    service_notifications_enabled: bool = True
    marketing_enabled: bool
    repeat_booking_enabled: bool


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
    staff_member_id: int = 1
    master_name: str | None = None
    price: Decimal | None = None
    duration_min_minutes: int | None = None
    duration_max_minutes: int | None = None
    prepayment_amount: Decimal | None = None


class BookableMasterView(BaseModel):
    """Client-safe master option assigned to one selected service."""

    model_config = ConfigDict(frozen=True)

    id: Annotated[int, Field(gt=0)]
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    specialization: str | None = None
    telegram_photo_file_id: str | None = None


class BookingMasterOptions(BaseModel):
    """Available masters plus the central selection switch."""

    selection_enabled: bool
    masters: list[BookableMasterView]


class BookingAvailability(BaseModel):
    """One service and all currently selectable matching windows."""

    service: ServiceView
    timezone: str
    windows: list[BookingWindowView]


class ReferenceMediaDraft(BaseModel):
    """Bounded Telegram metadata held in Redis until booking confirmation."""

    telegram_file_id: Annotated[str, Field(min_length=1, max_length=512)]
    telegram_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: MediaType = MediaType.PHOTO


class ReferenceMediaView(ReferenceMediaDraft):
    id: int
    position: Annotated[int, Field(ge=0)]


class ReferenceMediaPolicy(BaseModel):
    max_media: Annotated[int, Field(ge=1, le=10)]
    edit_deadline_hours: Annotated[int, Field(ge=1, le=720)]


class BookingRequest(BaseModel):
    """Final client-entered values submitted for transactional revalidation."""

    service_id: Annotated[int, Field(gt=0)]
    window_id: Annotated[int, Field(gt=0)]
    staff_member_id: Annotated[int, Field(gt=0)] | None = None
    client_name: Annotated[str, Field(min_length=1, max_length=255)]
    phone: Annotated[str, Field(max_length=32)]
    client_comment: Annotated[str, Field(max_length=2000)] | None = None
    design_reference_id: Annotated[int, Field(gt=0)] | None = None
    reference_media: Annotated[list[ReferenceMediaDraft], Field(max_length=10)] = Field(
        default_factory=list
    )
    checkout_idempotency_key: Annotated[str, Field(min_length=16, max_length=128)] = Field(
        default_factory=lambda: f"tg-booking:{token_urlsafe(24)}",
        repr=False,
    )
    reservation_token: SecretStr = Field(
        default_factory=lambda: SecretStr(token_urlsafe(32)),
        repr=False,
    )

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

    @field_validator("checkout_idempotency_key")
    @classmethod
    def validate_checkout_idempotency_key(cls, value: str) -> str:
        if (
            not value.isascii()
            or not value[0].isalnum()
            or any(not (character.isalnum() or character in "._:-") for character in value)
        ):
            raise ValueError("checkout idempotency key contains unsupported characters")
        return value


class BookingReceipt(BaseModel):
    """Committed booking details safe to render to the client or administrator."""

    appointment_id: int
    service_name: str
    master_name: str | None = None
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
    design_title: str | None = None
    reference_media: list[ReferenceMediaView] = Field(default_factory=list)
    appointment_status: AppointmentStatus = AppointmentStatus.CONFIRMED
    payment_mode: PaymentMode = PaymentMode.DISABLED
    payment_id: int | None = None
    payment_status: PaymentStatus | None = None
    payment_amount: Decimal | None = None
    payment_currency: str | None = None
    payment_confirmation_url: str | None = None
    reservation_expires_at: datetime | None = None
    manual_payment_instructions: str | None = None
