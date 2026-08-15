"""Self-scoped projections for a master's Telegram workspace."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (
    AppointmentStatus,
    ScheduleExceptionKind,
    ScheduleIntervalKind,
)


class MasterAppointmentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    appointment_id: int
    service_name: str
    client_name: str
    client_phone: str | None
    start_at: datetime
    end_at: datetime
    timezone: str
    status: AppointmentStatus


class MasterWeeklyIntervalView(BaseModel):
    model_config = ConfigDict(frozen=True)

    weekday: int
    kind: ScheduleIntervalKind
    start_minute: int
    end_minute: int


class MasterScheduleExceptionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    local_date: date
    kind: ScheduleExceptionKind
    start_minute: int | None
    end_minute: int | None
    reason: str | None


class MasterScheduleView(BaseModel):
    model_config = ConfigDict(frozen=True)

    timezone: str
    paused_until: datetime | None
    weekly_intervals: tuple[MasterWeeklyIntervalView, ...]
    upcoming_exceptions: tuple[MasterScheduleExceptionView, ...]
