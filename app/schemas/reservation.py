"""Validated reservation and booking-abuse policy inputs."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")


class ReservationCreate(BaseModel):
    """Immutable slot-hold identifiers supplied inside one DB transaction."""

    business_id: Annotated[int, Field(gt=0)]
    client_id: Annotated[int, Field(gt=0)]
    staff_member_id: Annotated[int, Field(gt=0)]
    window_id: Annotated[int, Field(gt=0)]
    service_id: Annotated[int, Field(gt=0)]
    appointment_id: Annotated[int, Field(gt=0)] | None = None
    idempotency_key: Annotated[str, Field(min_length=16, max_length=128)]
    ttl_minutes: Annotated[int, Field(ge=5, le=60)] = 20
    correlation_id: Annotated[str, Field(max_length=64)] | None = None

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if not _IDEMPOTENCY_PATTERN.fullmatch(value):
            raise ValueError("idempotency key must be 16-128 safe ASCII characters")
        return value
