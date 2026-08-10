"""Safe acquisition source and funnel projections."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AcquisitionSourceView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int = Field(gt=0)
    code: str
    display_name: str
    channel: str | None
    is_active: bool


class AcquisitionMetricsView(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: AcquisitionSourceView
    clients_arrived: int = Field(ge=0)
    clients_started_booking: int = Field(ge=0)
    clients_completed_booking: int = Field(ge=0)
    repeat_clients: int = Field(ge=0)


class AcquisitionLinkView(BaseModel):
    """A public campaign link; the same URL is the payload encoded by a QR renderer."""

    model_config = ConfigDict(frozen=True)

    source: AcquisitionSourceView
    deep_link: str
    qr_payload: str
