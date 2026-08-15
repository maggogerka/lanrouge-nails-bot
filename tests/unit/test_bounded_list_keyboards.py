"""Regression checks that growing Telegram lists stay below one screen page."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    ManualPaymentStatus,
    PaymentMode,
    PaymentStatus,
)
from app.domain.payments import PaymentType
from app.keyboards.admin.appointments import admin_appointment_list_keyboard
from app.keyboards.admin.business import (
    workstation_details_keyboard,
    workstation_list_keyboard,
)
from app.keyboards.admin.payments import payment_admin_list_keyboard
from app.keyboards.admin.services import service_list_keyboard
from app.keyboards.admin.windows import window_list_keyboard
from app.keyboards.client.appointments import appointment_list_keyboard
from app.keyboards.client.booking import BookingCallback, service_card_keyboard
from app.schemas.appointment import AdminAppointmentView, AppointmentView
from app.schemas.availability import AvailabilityWindowView
from app.schemas.payment import PaymentAdminSection, PaymentAdminView
from app.schemas.service import ServiceView
from app.schemas.workstation import WorkstationServiceView, WorkstationView

NOW = datetime(2026, 8, 15, 9, tzinfo=UTC)


def _service(item_id: int) -> ServiceView:
    return ServiceView(
        id=item_id,
        name=f"Service {item_id}",
        description=None,
        price=Decimal("1000"),
        duration_min_minutes=60,
        duration_max_minutes=60,
        is_active=True,
    )


def _appointment(item_id: int, *, admin: bool) -> AppointmentView:
    values = dict(
        id=item_id,
        service_name=f"Service {item_id}",
        master_name="Master",
        price=Decimal("1000"),
        duration_min_minutes=60,
        duration_max_minutes=60,
        status=AppointmentStatus.CONFIRMED,
        start_at=NOW + timedelta(hours=item_id),
        end_at=NOW + timedelta(hours=item_id + 1),
        timezone="Europe/Moscow",
        address="Address",
        can_self_manage=True,
    )
    if admin:
        return AdminAppointmentView(
            **values,
            client_name=f"Client {item_id}",
            client_phone=None,
            client_username=None,
        )
    return AppointmentView(**values)


def test_service_and_appointment_pages_remain_bounded() -> None:
    services = service_list_keyboard([_service(index) for index in range(8)], page=2, pages=9)
    client_appointments = appointment_list_keyboard(
        [_appointment(index, admin=False) for index in range(8)], page=2, pages=9
    )
    admin_appointments = admin_appointment_list_keyboard(
        [_appointment(index, admin=True) for index in range(8)],
        list_action="upcoming",
        page=2,
        pages=9,
    )

    assert len(services.inline_keyboard) <= 11
    assert len(client_appointments.inline_keyboard) <= 10
    assert len(admin_appointments.inline_keyboard) <= 18
    assert any(button.text == "2/9" for row in services.inline_keyboard for button in row)


def test_window_and_payment_pages_remain_bounded() -> None:
    windows = [
        AvailabilityWindowView(
            id=index,
            start_at=NOW + timedelta(hours=index),
            end_at=NOW + timedelta(hours=index + 1),
            timezone="Europe/Moscow",
            status=AvailabilityWindowStatus.OPEN,
            admin_comment=None,
        )
        for index in range(1, 9)
    ]
    payments = tuple(
        PaymentAdminView(
            id=index,
            business_id=1,
            appointment_id=index,
            provider=PaymentMode.MANUAL,
            provider_payment_id=None,
            amount=Decimal("500"),
            refunded_amount=Decimal("0"),
            currency="RUB",
            status=PaymentStatus.PENDING,
            payment_type=PaymentType.DEPOSIT,
            confirmation_url=None,
            expires_at=None,
            paid_at=None,
            cancelled_at=None,
            refunded_at=None,
            manual_status=ManualPaymentStatus.REVIEW_PENDING,
            created_at=NOW,
            appointment_start_at=NOW + timedelta(hours=index),
            timezone="Europe/Moscow",
            service_name="Service",
            master_name="Master",
            client_name=f"Client {index}",
            client_telegram_id=1000 + index,
        )
        for index in range(1, 9)
    )

    window_keyboard = window_list_keyboard(windows, page=2, pages=5)
    payment_keyboard = payment_admin_list_keyboard(
        payments, PaymentAdminSection.ACTIVE, page=2, pages=5
    )

    assert len(window_keyboard.inline_keyboard) <= 11
    assert len(payment_keyboard.inline_keyboard) <= 10


def test_service_browser_callbacks_fit_telegram_limit_with_pagination() -> None:
    callback = BookingCallback(
        action="service_page",
        object_id=9_223_372_036_854_775_807,
        page=2_147_483_647,
    ).pack()
    keyboard = service_card_keyboard(1, page=2, pages=20, has_photo=True)

    assert len(callback.encode()) <= 64
    assert len(keyboard.inline_keyboard) == 4


def test_workstation_and_its_service_assignments_are_bounded() -> None:
    services = tuple(
        WorkstationServiceView(
            service_id=index,
            service_name=f"Service {index}",
            service_active=True,
            enabled=index % 2 == 0,
        )
        for index in range(1, 40)
    )
    workstations = tuple(
        WorkstationView(id=index, name=f"Desk {index}", is_active=True) for index in range(1, 9)
    )

    listing = workstation_list_keyboard(workstations, page=2, pages=5)
    details = workstation_details_keyboard(
        WorkstationView(id=1, name="Desk", is_active=True, services=services), page=2
    )

    assert len(listing.inline_keyboard) <= 10
    assert len(details.inline_keyboard) <= 11
    assert any(button.text == "2/5" for row in listing.inline_keyboard for button in row)
    assert any(button.text == "2/5" for row in details.inline_keyboard for button in row)
