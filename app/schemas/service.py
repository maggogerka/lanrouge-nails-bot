"""Validated DTOs for the service catalog boundary."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
PositiveMinutes = Annotated[int, Field(gt=0, le=24 * 60)]


class AdminActor(BaseModel):
    """Safe Telegram identity data passed into an admin use case."""

    telegram_id: Annotated[int, Field(gt=0)]
    username: Annotated[str, Field(max_length=64)] | None = None
    first_name: Annotated[str, Field(max_length=255)] | None = None
    last_name: Annotated[str, Field(max_length=255)] | None = None


class ServiceCreate(BaseModel):
    """Complete validated values required to create a service."""

    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(max_length=4000)] | None = None
    price: Money
    duration_min_minutes: PositiveMinutes
    duration_max_minutes: PositiveMinutes
    prepayment_amount: Money = Decimal("0.00")
    telegram_photo_file_id: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    telegram_photo_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_duration_range(self) -> Self:
        if self.duration_min_minutes > self.duration_max_minutes:
            raise ValueError("minimum duration must not exceed maximum duration")
        if self.prepayment_amount > self.price:
            raise ValueError("prepayment must not exceed service price")
        if (self.telegram_photo_file_id is None) != (self.telegram_photo_file_unique_id is None):
            raise ValueError("photo id and unique id must be supplied together")
        return self


class ServicePatch(BaseModel):
    """Partial editable values; active state has dedicated operations."""

    name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    description: Annotated[str, Field(max_length=4000)] | None = None
    price: Money | None = None
    duration_min_minutes: PositiveMinutes | None = None
    duration_max_minutes: PositiveMinutes | None = None
    prepayment_amount: Money | None = None
    telegram_photo_file_id: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    telegram_photo_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one service field must be supplied")
        required_when_supplied = (
            "name",
            "price",
            "duration_min_minutes",
            "duration_max_minutes",
            "prepayment_amount",
        )
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in required_when_supplied
        ):
            raise ValueError("editable service field must not be null")
        photo_fields = {
            "telegram_photo_file_id",
            "telegram_photo_file_unique_id",
        }
        if self.model_fields_set & photo_fields and not photo_fields <= self.model_fields_set:
            raise ValueError("photo id and unique id must be supplied together")
        if (self.telegram_photo_file_id is None) != (self.telegram_photo_file_unique_id is None):
            raise ValueError("photo id and unique id must be supplied together")
        return self


class ServiceView(BaseModel):
    """Transport-safe catalog representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    price: Decimal
    duration_min_minutes: int
    duration_max_minutes: int
    prepayment_amount: Decimal = Decimal("0.00")
    telegram_photo_file_id: str | None = None
    telegram_photo_file_unique_id: str | None = None
    is_active: bool

    @field_validator("prepayment_amount", mode="before")
    @classmethod
    def normalize_legacy_prepayment(cls, value: object) -> object:
        """Treat pre-migration/in-memory services as having no prepayment."""
        return Decimal("0.00") if value is None else value


class ServiceAddonCreate(BaseModel):
    """Validated fields for one optional addition to a base service."""

    service_id: Annotated[int, Field(gt=0)]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(max_length=4000)] | None = None
    price: Money
    duration_min_minutes: PositiveMinutes
    duration_max_minutes: PositiveMinutes
    telegram_photo_file_id: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    telegram_photo_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    sort_order: Annotated[int, Field(ge=0, le=1_000_000)] = 0

    @field_validator("name", mode="before")
    @classmethod
    def normalize_addon_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", mode="before")
    @classmethod
    def normalize_addon_description(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_addon(self) -> Self:
        if self.duration_min_minutes > self.duration_max_minutes:
            raise ValueError("minimum duration must not exceed maximum duration")
        if (self.telegram_photo_file_id is None) != (self.telegram_photo_file_unique_id is None):
            raise ValueError("photo id and unique id must be supplied together")
        return self


class ServiceAddonPatch(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    description: Annotated[str, Field(max_length=4000)] | None = None
    price: Money | None = None
    duration_min_minutes: PositiveMinutes | None = None
    duration_max_minutes: PositiveMinutes | None = None
    telegram_photo_file_id: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    telegram_photo_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    sort_order: Annotated[int, Field(ge=0, le=1_000_000)] | None = None

    @model_validator(mode="after")
    def require_addon_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one add-on field must be supplied")
        non_nullable = {
            "name",
            "price",
            "duration_min_minutes",
            "duration_max_minutes",
            "sort_order",
        }
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in non_nullable
        ):
            raise ValueError("editable add-on field must not be null")
        photo_fields = {
            "telegram_photo_file_id",
            "telegram_photo_file_unique_id",
        }
        if self.model_fields_set & photo_fields and not photo_fields <= self.model_fields_set:
            raise ValueError("photo id and unique id must be supplied together")
        if (self.telegram_photo_file_id is None) != (self.telegram_photo_file_unique_id is None):
            raise ValueError("photo id and unique id must be supplied together")
        return self


class ServiceAddonView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    service_id: int
    name: str
    description: str | None
    price: Decimal
    duration_min_minutes: int
    duration_max_minutes: int
    telegram_photo_file_id: str | None = None
    sort_order: int = 0
    is_active: bool


class AppointmentAddonView(BaseModel):
    """Immutable add-on snapshot safe to show in booking history."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    service_addon_id: int
    name_snapshot: str
    description_snapshot: str | None = None
    price_snapshot: Decimal
    duration_min_snapshot: int
    duration_max_snapshot: int
    position: int
