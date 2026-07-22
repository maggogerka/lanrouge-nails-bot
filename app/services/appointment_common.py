"""Shared appointment view and authorization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.database.models import Appointment, AvailabilityWindow, BusinessSettings, User
from app.domain.errors import AppointmentNotFoundError, AuthorizationError
from app.schemas.appointment import AdminAppointmentView, AppointmentView
from app.schemas.service import AdminActor


def ensure_admin(actor: AdminActor, admin_telegram_ids: frozenset[int]) -> None:
    if actor.telegram_id not in admin_telegram_ids:
        raise AuthorizationError("Administrative access denied")


def ensure_owner(appointment: Appointment, client: User) -> None:
    """Use a not-found response to avoid disclosing another client's record."""

    if appointment.client_id != client.id:
        raise AppointmentNotFoundError("Запись не найдена.")


def appointment_view(
    appointment: Appointment,
    window: AvailabilityWindow,
    settings: BusinessSettings,
    now: datetime,
) -> AppointmentView:
    return AppointmentView(
        id=appointment.id,
        service_name=appointment.service_name_snapshot,
        price=appointment.price_snapshot,
        duration_min_minutes=appointment.duration_min_snapshot,
        duration_max_minutes=appointment.duration_max_snapshot,
        status=appointment.status,
        start_at=window.start_at,
        end_at=window.end_at,
        timezone=settings.timezone,
        address=settings.address,
        map_url=settings.map_url,
        master_telegram_url=settings.master_telegram_url,
        can_self_manage=(
            window.start_at - now >= timedelta(hours=settings.cancellation_deadline_hours)
        ),
    )


def admin_appointment_view(
    appointment: Appointment,
    window: AvailabilityWindow,
    client: User,
    settings: BusinessSettings,
    now: datetime,
) -> AdminAppointmentView:
    common = appointment_view(appointment, window, settings, now)
    return AdminAppointmentView(
        **common.model_dump(),
        client_name=client.first_name or "—",
        client_phone=client.phone,
        client_username=client.username,
    )
