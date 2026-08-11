"""Validated application DTOs for payments, refunds and webhook intake."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import ManualPaymentStatus, PaymentMode, PaymentStatus, RefundStatus
from app.domain.payments import PaymentType, validate_money, validate_safe_metadata

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_REASON_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PaymentCreate(BaseModel):
    """Immutable values used to create one local payment intent."""

    business_id: Annotated[int, Field(gt=0)]
    appointment_id: Annotated[int, Field(gt=0)]
    provider: PaymentMode
    payment_type: PaymentType
    amount: Decimal
    currency: str = "RUB"
    idempotency_key: Annotated[str, Field(min_length=16, max_length=128)]
    safe_metadata: dict[str, str] = Field(default_factory=dict)
    correlation_id: Annotated[str, Field(max_length=64)] | None = None
    return_url: AnyHttpUrl | None = None
    description: Annotated[str, Field(min_length=1, max_length=128)] = "Оплата услуги"

    @field_validator("provider")
    @classmethod
    def disabled_is_not_a_provider(cls, value: PaymentMode) -> PaymentMode:
        if value is PaymentMode.DISABLED:
            raise ValueError("disabled payment mode cannot create a payment")
        return value

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return validate_money(value, "RUB")[0]

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return validate_money(Decimal("1"), value)[1]

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not _IDEMPOTENCY_PATTERN.fullmatch(value):
            raise ValueError("idempotency key must be 16-128 safe ASCII characters")
        return value

    @field_validator("safe_metadata")
    @classmethod
    def metadata_is_bounded_and_safe(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_safe_metadata(value)

    @field_validator("return_url")
    @classmethod
    def return_url_uses_https(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("payment return URL must use HTTPS")
        return value


class RefundCreate(BaseModel):
    """Validated request for a full or partial refund."""

    business_id: Annotated[int, Field(gt=0)]
    payment_id: Annotated[int, Field(gt=0)]
    amount: Decimal
    currency: str = "RUB"
    idempotency_key: Annotated[str, Field(min_length=16, max_length=128)]
    reason_code: Annotated[str, Field(min_length=1, max_length=64)] = "requested_by_business"
    requested_by_user_id: Annotated[int, Field(gt=0)] | None = None
    correlation_id: Annotated[str, Field(max_length=64)] | None = None
    safe_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return validate_money(value, "RUB")[0]

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return validate_money(Decimal("1"), value)[1]

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not _IDEMPOTENCY_PATTERN.fullmatch(value):
            raise ValueError("idempotency key must be 16-128 safe ASCII characters")
        return value

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        if not _REASON_CODE_PATTERN.fullmatch(value):
            raise ValueError("refund reason must be a non-sensitive machine code")
        return value

    @field_validator("safe_metadata")
    @classmethod
    def metadata_is_bounded_and_safe(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_safe_metadata(value)


class PaymentView(BaseModel):
    """Safe payment projection; credentials and provider payloads do not exist here."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    business_id: int
    appointment_id: int
    provider: PaymentMode
    provider_payment_id: str | None
    amount: Decimal
    refunded_amount: Decimal
    currency: str
    status: PaymentStatus
    payment_type: PaymentType
    confirmation_url: str | None
    expires_at: datetime | None
    paid_at: datetime | None
    cancelled_at: datetime | None
    refunded_at: datetime | None
    manual_status: ManualPaymentStatus | None = None
    client_reported_at: datetime | None = None
    review_started_at: datetime | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    has_receipt: bool = False


class ManualReceiptDraft(BaseModel):
    """One bounded Telegram receipt reference; the file itself is never downloaded."""

    telegram_file_id: Annotated[str, Field(min_length=1, max_length=512)] = Field(repr=False)
    telegram_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: str
    file_size: Annotated[int, Field(gt=0, le=20 * 1024 * 1024)]

    @field_validator("media_type")
    @classmethod
    def supported_media_type(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"photo", "document"}:
            raise ValueError("receipt must be a photo or supported document")
        return normalized


class PaymentSettingsView(BaseModel):
    """Safe business payment policy without provider credentials."""

    model_config = ConfigDict(from_attributes=True)

    business_id: int
    mode: PaymentMode
    manual_payment_instructions: str | None
    reservation_ttl_minutes: int
    client_payment_reminder_minutes: list[int] = Field(default_factory=lambda: [5, 10])
    staff_review_reminder_minutes: list[int] = Field(default_factory=lambda: [30, 120])
    client_payment_reminders_enabled: bool = True
    staff_payment_notifications_enabled: bool = True
    cancellation_refund_deadline_hours: int
    late_cancellation_refund_percent: int
    version: int


class RefundView(BaseModel):
    """Safe refund projection for Telegram and the future HTTP API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    business_id: int
    payment_id: int
    provider: PaymentMode
    provider_refund_id: str | None
    amount: Decimal
    currency: str
    status: RefundStatus
    reason_code: str
    succeeded_at: datetime | None
    failed_at: datetime | None


def validate_webhook_mapping(value: Any) -> dict[str, object]:
    """Reject non-object decoded input before it reaches a provider parser."""

    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("webhook payload must be a JSON object")
    return value
