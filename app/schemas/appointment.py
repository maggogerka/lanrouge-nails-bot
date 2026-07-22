"""Transport-safe appointment lifecycle DTOs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.enums import AppointmentStatus
from app.schemas.booking import BookingWindowView


class AppointmentView(BaseModel):
    """Appointment projection visible to its client."""

    id: int
    service_name: str
    price: Decimal
    duration_min_minutes: int
    duration_max_minutes: int
    status: AppointmentStatus
    start_at: datetime
    end_at: datetime
    timezone: str
    address: str
    map_url: str
    master_telegram_url: str
    can_self_manage: bool


class AdminAppointmentView(AppointmentView):
    """Appointment projection with client contact for authorized administrators."""

    client_name: str
    client_phone: str | None
    client_username: str | None


class RescheduleAvailability(BaseModel):
    """Current appointment and compatible replacement windows."""

    appointment: AppointmentView
    windows: list[BookingWindowView]
