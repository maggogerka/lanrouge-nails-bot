"""Validated waitlist commands and transport-neutral views."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import WaitlistStatus


class WaitlistCreate(BaseModel):
    service_id: Annotated[int, Field(gt=0)]
    date_from: date
    date_to: date
    preferred_dates: list[date] = Field(default_factory=list, max_length=31)
    preferred_time_from: time | None = None
    preferred_time_to: time | None = None

    @model_validator(mode="after")
    def validate_preferences(self) -> WaitlistCreate:
        if self.date_from > self.date_to:
            raise ValueError("Дата начала не может быть позже даты окончания.")
        if (self.date_to - self.date_from).days > 180:
            raise ValueError("Период ожидания не может превышать 180 дней.")
        if any(value < self.date_from or value > self.date_to for value in self.preferred_dates):
            raise ValueError("Предпочтительные даты должны входить в выбранный период.")
        if (self.preferred_time_from is None) != (self.preferred_time_to is None):
            raise ValueError("Укажите начало и окончание желаемого времени.")
        if (
            self.preferred_time_from is not None
            and self.preferred_time_to is not None
            and self.preferred_time_from >= self.preferred_time_to
        ):
            raise ValueError("Начало желаемого времени должно быть раньше окончания.")
        self.preferred_dates = sorted(set(self.preferred_dates))
        return self


class WaitlistView(BaseModel):
    id: int
    service_id: int
    service_name: str
    date_from: date
    date_to: date
    preferred_dates: list[date]
    preferred_time_from: time | None
    preferred_time_to: time | None
    status: WaitlistStatus
    expires_at: datetime


class AdminWaitlistView(WaitlistView):
    client_id: int
    client_name: str
    client_telegram_id: int


class WaitlistDelivery(BaseModel):
    notification_id: int
    entry_id: int
    window_id: int
    recipient_user_id: int
    recipient_telegram_id: int
    service_name: str
    start_at: datetime
    timezone: str
    attempts: int
