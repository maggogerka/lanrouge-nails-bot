"""Shared appointment view and authorization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.database.models import Appointment, AvailabilityWindow, BusinessSettings, User
from app.domain.errors import AppointmentNotFoundError, AuthorizationError
from app.schemas.appointment import AdminAppointmentView, AppointmentView
from app.schemas.service import AdminActor
from app.security import (
    LEGACY_ADMIN_ROLES,
    get_staff_context,
    is_db_staff_authorization_required,
)


def ensure_admin(actor: AdminActor, admin_telegram_ids: frozenset[int]) -> None:
    """Authorize legacy CRUD through a fresh runtime context.

    The numeric-ID branch exists only so isolated service unit tests and offline
    callers remain compatible. Protected Telegram routers always bind a DB
    context and therefore never fall back to environment configuration.
    """

    context = get_staff_context()
    if context is not None:
        if context.telegram_id != actor.telegram_id or context.role not in LEGACY_ADMIN_ROLES:
            raise AuthorizationError("Administrative access denied")
        return
    if is_db_staff_authorization_required() or actor.telegram_id not in admin_telegram_ids:
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
