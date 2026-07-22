"""Validated administrator business-rule settings DTOs."""

from __future__ import annotations

from typing import Annotated, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import PortfolioDisplayMode


class BusinessSettingsView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_name: str
    timezone: str
    address: str
    map_url: str
    master_telegram_url: str
    booking_horizon_days: int
    cancellation_deadline_hours: int
    max_appointments_per_day: int
    default_window_duration_minutes: int
    minimum_gap_minutes: int
    allow_saturday: bool
    allow_sunday: bool
    reminder_offsets_minutes: list[int]
    portfolio_page_size: int = 5
    portfolio_max_media: int = 8
    waitlist_default_expiration_days: int = 31
    waitlist_notification_cooldown_minutes: int = 180
    review_request_delay_minutes: int = 60
    repeat_booking_reminder_days: int = 28
    broadcast_messages_per_second: int = 15
    broadcast_max_media: int = 5
    broadcast_max_retries: int = 5
    broadcast_retry_base_seconds: int = 15
    client_page_size: int = 10
    reviews_enabled: bool = True
    waitlist_enabled: bool = True
    broadcasts_enabled: bool = False
    portfolio_enabled: bool = True
    availability_date_picker_days: int = 31
    availability_time_step_minutes: int = 60
    booking_reference_max_media: int = 10
    booking_reference_edit_deadline_hours: int = 36
    booking_reference_retention_days: int | None = None
    portfolio_mode: PortfolioDisplayMode = PortfolioDisplayMode.INTERNAL
    external_portfolio_url: str | None = None
    external_portfolio_button_text: str = "Открыть портфолио"
    master_profile_enabled: bool = True
    version: int


class BusinessSettingsPatch(BaseModel):
    booking_horizon_days: Annotated[int, Field(gt=0, le=365)] | None = None
    cancellation_deadline_hours: Annotated[int, Field(gt=0, le=24 * 30)] | None = None
    max_appointments_per_day: Annotated[int, Field(gt=0, le=20)] | None = None
    default_window_duration_minutes: Annotated[int, Field(gt=0, le=24 * 60)] | None = None
    minimum_gap_minutes: Annotated[int, Field(ge=0, le=24 * 60)] | None = None
    allow_saturday: bool | None = None
    allow_sunday: bool | None = None
    reminder_offsets_minutes: list[Annotated[int, Field(gt=0, le=60 * 24 * 30)]] | None = None
    portfolio_page_size: Annotated[int, Field(ge=1, le=20)] | None = None
    portfolio_max_media: Annotated[int, Field(ge=1, le=10)] | None = None
    waitlist_default_expiration_days: Annotated[int, Field(ge=1, le=180)] | None = None
    waitlist_notification_cooldown_minutes: Annotated[int, Field(ge=0, le=10080)] | None = None
    review_request_delay_minutes: Annotated[int, Field(ge=0, le=10080)] | None = None
    repeat_booking_reminder_days: Annotated[int, Field(ge=1, le=365)] | None = None
    broadcast_messages_per_second: Annotated[int, Field(ge=1, le=20)] | None = None
    broadcast_max_media: Annotated[int, Field(ge=0, le=10)] | None = None
    broadcast_max_retries: Annotated[int, Field(ge=0, le=20)] | None = None
    broadcast_retry_base_seconds: Annotated[int, Field(ge=1, le=3600)] | None = None
    client_page_size: Annotated[int, Field(ge=1, le=50)] | None = None
    reviews_enabled: bool | None = None
    waitlist_enabled: bool | None = None
    broadcasts_enabled: bool | None = None
    portfolio_enabled: bool | None = None
    availability_date_picker_days: Annotated[int, Field(ge=1, le=62)] | None = None
    availability_time_step_minutes: Annotated[int, Field(ge=1, le=1440)] | None = None
    booking_reference_max_media: Annotated[int, Field(ge=1, le=10)] | None = None
    booking_reference_edit_deadline_hours: Annotated[int, Field(ge=1, le=720)] | None = None
    booking_reference_retention_days: Annotated[int, Field(ge=1, le=3650)] | None = None
    portfolio_mode: PortfolioDisplayMode | None = None
    external_portfolio_url: Annotated[str, Field(max_length=2048)] | None = None
    external_portfolio_button_text: Annotated[str, Field(min_length=1, max_length=100)] | None = (
        None
    )
    master_profile_enabled: bool | None = None

    @field_validator("availability_time_step_minutes")
    @classmethod
    def time_step_must_divide_day(cls, value: int | None) -> int | None:
        if value is not None and (24 * 60) % value:
            raise ValueError("time step must divide 1440 minutes")
        return value

    @field_validator("external_portfolio_url")
    @classmethod
    def external_portfolio_url_must_be_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("external portfolio URL must be an absolute HTTPS URL")
        return normalized

    @model_validator(mode="after")
    def require_exactly_one_change(self) -> Self:
        if len(self.model_fields_set) != 1:
            raise ValueError("exactly one setting must be supplied")
        field = next(iter(self.model_fields_set))
        nullable_fields = {"booking_reference_retention_days", "external_portfolio_url"}
        if getattr(self, field) is None and field not in nullable_fields:
            raise ValueError("setting must not be null")
        if field == "reminder_offsets_minutes":
            offsets = self.reminder_offsets_minutes or []
            if not offsets or len(offsets) != len(set(offsets)):
                raise ValueError("reminder offsets must be a non-empty unique list")
        return self
