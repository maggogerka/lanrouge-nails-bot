"""Validated DTOs for manual availability windows."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import AvailabilityWindowStatus


class AvailabilityWindowCreate(BaseModel):
    """Administrator input expressed in the business timezone."""

    local_date: date
    local_start_time: time
    service_id: Annotated[int, Field(gt=0)]
    staff_member_id: Annotated[int, Field(gt=0)] | None = None
    duration_minutes: Annotated[int, Field(gt=0, le=24 * 60)] | None = None
    admin_comment: Annotated[str, Field(max_length=2000)] | None = None
    status: AvailabilityWindowStatus = AvailabilityWindowStatus.OPEN

    @field_validator("admin_comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("local_start_time")
    @classmethod
    def local_time_must_be_naive(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("local start time must not contain a timezone")
        return value

    @model_validator(mode="after")
    def only_open_or_closed_can_be_created(self) -> Self:
        if self.status not in {
            AvailabilityWindowStatus.OPEN,
            AvailabilityWindowStatus.CLOSED,
        }:
            raise ValueError("new window status must be open or closed")
        return self


class AvailabilityWindowView(BaseModel):
    """UTC interval plus timezone required for deterministic presentation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    staff_member_id: int = 1
    master_name: str | None = None
    service_id: int | None = None
    service_name: str | None = None
    workstation_id: int | None = None
    workstation_name: str | None = None
    start_at: datetime
    end_at: datetime
    status: AvailabilityWindowStatus
    admin_comment: str | None
    timezone: str

    @model_validator(mode="after")
    def timestamps_must_be_aware(self) -> Self:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("window timestamps must be timezone-aware")
        return self


class AvailabilityWindowPreview(BaseModel):
    """Validated interval used by the explicit creation confirmation screen."""

    start_at: datetime
    end_at: datetime
    service_id: int
    service_name: str
    workstation_id: int
    workstation_name: str
    duration_minutes: int
    admin_comment: str | None
    timezone: str

    @model_validator(mode="after")
    def timestamps_must_be_aware(self) -> Self:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("preview timestamps must be timezone-aware")
        return self


class AvailabilityWindowList(BaseModel):
    """Upcoming admin schedule page."""

    timezone: str
    windows: list[AvailabilityWindowView]
