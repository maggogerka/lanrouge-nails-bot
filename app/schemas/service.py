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
        return self


class ServicePatch(BaseModel):
    """Partial editable values; active state has dedicated operations."""

    name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    description: Annotated[str, Field(max_length=4000)] | None = None
    price: Money | None = None
    duration_min_minutes: PositiveMinutes | None = None
    duration_max_minutes: PositiveMinutes | None = None
    prepayment_amount: Money | None = None

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
    is_active: bool

    @field_validator("prepayment_amount", mode="before")
    @classmethod
    def normalize_legacy_prepayment(cls, value: object) -> object:
        """Treat pre-migration/in-memory services as having no prepayment."""
        return Decimal("0.00") if value is None else value
