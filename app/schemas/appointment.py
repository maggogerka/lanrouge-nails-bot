"""Transport-safe appointment lifecycle DTOs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.domain.enums import AppointmentStatus, PaymentMode
from app.schemas.booking import BookingWindowView


class AppointmentView(BaseModel):
    """Appointment projection visible to its client."""

    id: int
    service_name: str
    master_name: str | None = None
    price: Decimal
    duration_min_minutes: int
    duration_max_minutes: int
    status: AppointmentStatus
    start_at: datetime
    end_at: datetime
    timezone: str
    address: str
    map_url: str | None = None
    master_telegram_url: str | None = None
    can_self_manage: bool
    can_reschedule: bool | None = None


class AdminAppointmentView(AppointmentView):
    """Appointment projection with client contact for authorized administrators."""

    client_name: str
    client_phone: str | None
    client_username: str | None
    client_telegram_id: int | None = None
    client_comment: str | None = None
    workstation_name: str | None = None
    prepayment_amount: Decimal = Decimal("0")
    payment_mode: PaymentMode = PaymentMode.DISABLED
    reservation_expires_at: datetime | None = None


class RescheduleAvailability(BaseModel):
    """Current appointment and compatible replacement windows."""

    appointment: AppointmentView
    windows: list[BookingWindowView]
