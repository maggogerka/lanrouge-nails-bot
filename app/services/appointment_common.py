"""Shared appointment view and authorization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.database.models import Appointment, AvailabilityWindow, BusinessSettings, User
from app.domain.enums import PaymentMode, StaffRole
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


def ensure_owner_admin(actor: AdminActor, admin_telegram_ids: frozenset[int]) -> None:
    """Restrict irreversible destructive actions to a verified business owner."""

    ensure_admin(actor, admin_telegram_ids)
    context = get_staff_context()
    if context is not None and context.role is not StaffRole.OWNER:
        raise AuthorizationError("Принудительное удаление доступно только владельцу бизнеса.")


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
        master_name=appointment.master_name_snapshot,
        price=appointment.price_snapshot,
        duration_min_minutes=appointment.duration_min_snapshot,
        duration_max_minutes=appointment.duration_max_snapshot,
        status=appointment.status,
        start_at=window.start_at,
        end_at=window.end_at,
        timezone=settings.timezone,
        address=appointment.address_snapshot or "Адрес не указан",
        map_url=appointment.map_url_snapshot,
        master_telegram_url=appointment.master_contact_url_snapshot,
        can_self_manage=(
            window.start_at - now >= timedelta(hours=settings.cancellation_deadline_hours)
        ),
        can_reschedule=(
            window.start_at - now
            >= timedelta(hours=getattr(settings, "reschedule_deadline_hours", None) or 24)
        ),
    )


def admin_appointment_view(
    appointment: Appointment,
    window: AvailabilityWindow,
    client: User,
    settings: BusinessSettings,
    now: datetime,
    *,
    workstation_name: str | None = None,
) -> AdminAppointmentView:
    common = appointment_view(appointment, window, settings, now)
    return AdminAppointmentView(
        **common.model_dump(),
        client_name=client.first_name or "—",
        client_phone=client.phone,
        client_username=client.username,
        client_telegram_id=client.telegram_id,
        client_comment=appointment.client_comment,
        workstation_name=workstation_name,
        prepayment_amount=appointment.prepayment_snapshot or Decimal("0"),
        payment_mode=appointment.payment_mode_snapshot or PaymentMode.DISABLED,
        reservation_expires_at=appointment.reservation_expires_at,
    )
