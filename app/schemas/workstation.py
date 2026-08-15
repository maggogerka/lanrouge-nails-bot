"""Validated workstation administration projections."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkstationCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=255)]

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class WorkstationServiceView(BaseModel):
    model_config = ConfigDict(frozen=True)

    service_id: int
    service_name: str
    service_active: bool
    enabled: bool


class WorkstationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    is_active: bool
    services: tuple[WorkstationServiceView, ...] = ()
