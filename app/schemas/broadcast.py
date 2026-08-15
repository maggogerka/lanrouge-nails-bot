"""Validated broadcast drafts, frozen delivery payloads and result counters."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

from app.domain.enums import (
    BroadcastAudienceType,
    BroadcastButtonType,
    BroadcastRecipientStatus,
    BroadcastStatus,
    MediaType,
)
from app.utils.telegram_text import require_telegram_message


class BroadcastMediaInput(BaseModel):
    telegram_file_id: Annotated[str, Field(min_length=1, max_length=512)]
    telegram_file_unique_id: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: MediaType = MediaType.PHOTO


class BroadcastCreate(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=255)]
    text: Annotated[str, Field(min_length=1, max_length=4096)]
    audience_type: BroadcastAudienceType = BroadcastAudienceType.ALL_SUBSCRIBED
    audience_parameters: dict[str, object] = Field(default_factory=dict)
    button_type: BroadcastButtonType = BroadcastButtonType.NONE
    button_text: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    button_url: AnyHttpUrl | None = None
    linked_portfolio_item_id: Annotated[int, Field(gt=0)] | None = None
    media: list[BroadcastMediaInput] = Field(default_factory=list, max_length=10)

    @field_validator("title", "text", "button_text", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("text")
    @classmethod
    def validate_telegram_message_length(cls, value: str) -> str:
        return require_telegram_message(value)

    @model_validator(mode="after")
    def validate_button_and_audience(self) -> BroadcastCreate:
        if self.button_type is BroadcastButtonType.URL:
            if self.button_url is None or self.button_text is None:
                raise ValueError("Для кнопки-ссылки нужны текст и HTTPS URL.")
            if self.button_url.scheme != "https":
                raise ValueError("Разрешены только HTTPS-ссылки.")
        elif self.button_url is not None:
            raise ValueError("URL разрешён только для кнопки-ссылки.")
        if self.button_type is not BroadcastButtonType.NONE and self.button_text is None:
            raise ValueError("Укажите текст кнопки.")
        required = {
            BroadcastAudienceType.CLIENT_TAG: "tag_id",
            BroadcastAudienceType.SERVICE_HISTORY: "service_id",
            BroadcastAudienceType.INACTIVE_DAYS: "days",
            BroadcastAudienceType.MANUAL: "user_ids",
        }.get(self.audience_type)
        if required is not None and required not in self.audience_parameters:
            raise ValueError(f"Для аудитории требуется параметр {required}.")
        return self


class BroadcastMediaView(BaseModel):
    telegram_file_id: str
    telegram_file_unique_id: str
    media_type: MediaType
    position: int


class BroadcastView(BaseModel):
    id: int
    title: str
    text: str
    status: BroadcastStatus
    audience_type: BroadcastAudienceType
    audience_parameters: dict[str, object]
    button_type: BroadcastButtonType
    button_text: str | None
    button_url: str | None
    linked_portfolio_item_id: int | None
    scheduled_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    media: list[BroadcastMediaView]
    created_at: datetime


class BroadcastResult(BaseModel):
    broadcast: BroadcastView
    total: int
    counts: dict[BroadcastRecipientStatus, int]


class BroadcastDelivery(BaseModel):
    recipient_id: int
    broadcast_id: int
    recipient_user_id: int
    recipient_telegram_id: int
    attempts: int
    text: str
    button_type: BroadcastButtonType
    button_text: str | None = None
    button_url: str | None = None
    media: list[BroadcastMediaView]
