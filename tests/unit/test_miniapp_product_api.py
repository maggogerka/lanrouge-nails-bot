"""Authenticated Mini App product routes delegate to shared application services."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.contracts import AsgiMessage, HttpRequest, SafeHttpError
from app.api.product import MiniAppProductApi
from app.api.sessions import OpaqueSession
from app.domain.enums import AppointmentStatus, PaymentMode, PaymentStatus
from app.domain.payments import PaymentType
from app.schemas.appointment import AppointmentView, RescheduleAvailability
from app.schemas.booking import BookingAvailability, BookingReceipt, BookingWindowView
from app.schemas.payment import PaymentView
from app.schemas.service import ServiceView
from app.services.appointment_service import AppointmentService
from app.services.booking_service import BookingService
from app.services.client_payment_service import ClientPaymentService
from app.services.consent_service import ConsentService
from app.services.presentation_service import PresentationService
from app.services.reschedule_service import RescheduleService

NOW = datetime(2026, 8, 11, 9, tzinfo=UTC)
SESSION = OpaqueSession(
    telegram_user_id=123_456,
    issued_at=NOW,
    expires_at=NOW + timedelta(minutes=15),
    auth_date=NOW,
)
SERVICE = ServiceView(
    id=5,
    name="Консультация",
    description=None,
    price=Decimal("2500.00"),
    duration_min_minutes=60,
    duration_max_minutes=90,
    is_active=True,
)
WINDOW = BookingWindowView(
    id=7,
    start_at=datetime(2026, 8, 15, 8, tzinfo=UTC),
    end_at=datetime(2026, 8, 15, 9, 30, tzinfo=UTC),
    timezone="Europe/Moscow",
    staff_member_id=3,
    master_name="Анна",
)
APPOINTMENT = AppointmentView(
    id=11,
    service_name="Консультация",
    price=Decimal("2500.00"),
    duration_min_minutes=60,
    duration_max_minutes=90,
    status=AppointmentStatus.CONFIRMED,
    start_at=WINDOW.start_at,
    end_at=WINDOW.end_at,
    timezone=WINDOW.timezone,
    address="Москва",
    map_url="https://example.test/map",
    master_telegram_url="https://t.me/master",
    can_self_manage=True,
)


def build_product() -> tuple[MiniAppProductApi, dict[str, MagicMock]]:
    dependencies = {
        name: MagicMock()
        for name in (
            "presentation",
            "booking",
            "appointments",
            "reschedule",
            "consents",
            "payments",
        )
    }
    product = MiniAppProductApi(
        presentation=cast(PresentationService, dependencies["presentation"]),
        booking=cast(BookingService, dependencies["booking"]),
        appointments=cast(AppointmentService, dependencies["appointments"]),
        reschedule=cast(RescheduleService, dependencies["reschedule"]),
        consents=cast(ConsentService, dependencies["consents"]),
        payments=cast(ClientPaymentService, dependencies["payments"]),
    )
    return product, dependencies


def request(
    *,
    method: str,
    path: str,
    query: bytes = b"",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> HttpRequest:
    delivered = False

    async def receive() -> AsgiMessage:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return HttpRequest(
        method=method,
        path=path,
        scheme="https",
        headers=headers or {},
        client_host="203.0.113.10",
        receive=receive,
        query_string=query,
    )


def payload(response_body: bytes) -> dict[str, object]:
    decoded = json.loads(response_body)
    assert isinstance(decoded, dict)
    return decoded


@pytest.mark.asyncio
async def test_catalog_and_available_dates_use_shared_booking_services() -> None:
    product, services = build_product()
    services["booking"].list_active_services = AsyncMock(return_value=[SERVICE])
    services["booking"].list_availability = AsyncMock(
        return_value=BookingAvailability(
            service=SERVICE,
            timezone="Europe/Moscow",
            windows=[WINDOW],
        )
    )

    catalog = await product.dispatch(
        request(method="GET", path="/api/v1/services"),
        SESSION,
        correlation_id="request-12345678",
    )
    dates = await product.dispatch(
        request(
            method="GET",
            path="/api/v1/availability/dates",
            query=b"service_id=5&staff_member_id=3",
        ),
        SESSION,
        correlation_id="request-12345678",
    )

    assert catalog is not None and payload(catalog.body)["services"][0]["id"] == 5  # type: ignore[index]
    assert dates is not None and payload(dates.body)["dates"] == ["2026-08-15"]
    actor = services["booking"].list_availability.await_args.args[0]
    assert actor.telegram_id == SESSION.telegram_user_id
    assert services["booking"].list_availability.await_args.kwargs["staff_member_id"] == 3


@pytest.mark.asyncio
async def test_booking_is_idempotent_and_never_reflects_reservation_secret() -> None:
    product, services = build_product()
    services["booking"].book = AsyncMock(
        return_value=BookingReceipt(
            appointment_id=11,
            service_name="Консультация",
            master_name="Анна",
            price=Decimal("2500.00"),
            duration_min_minutes=60,
            duration_max_minutes=90,
            start_at=WINDOW.start_at,
            end_at=WINDOW.end_at,
            timezone="Europe/Moscow",
            address="Москва",
            map_url="https://example.test/map",
            master_telegram_url="https://t.me/master",
            client_name="Клиент",
            phone="+79990000000",
            appointment_status=AppointmentStatus.PENDING_PAYMENT,
            payment_mode=PaymentMode.YOOKASSA,
            payment_id=21,
            payment_status=PaymentStatus.PENDING,
        )
    )
    secret = "s" * 43
    response = await product.dispatch(
        request(
            method="POST",
            path="/api/v1/reservations",
            headers={
                "content-type": "application/json",
                "idempotency-key": "miniapp-booking:request-12345678",
            },
            body=json.dumps(
                {
                    "service_id": 5,
                    "window_id": 7,
                    "staff_member_id": 3,
                    "client_name": "Клиент",
                    "phone": "+79990000000",
                    "reservation_token": secret,
                }
            ).encode(),
        ),
        SESSION,
        correlation_id="request-12345678",
    )

    assert response is not None and response.status_code == 201
    assert secret.encode() not in response.body
    values = services["booking"].book.await_args.args[1]
    assert values.checkout_idempotency_key == "miniapp-booking:request-12345678"
    assert values.reservation_token.get_secret_value() == secret


@pytest.mark.asyncio
async def test_personal_appointment_payment_cancel_and_reschedule_routes() -> None:
    product, services = build_product()
    services["appointments"].list_my = AsyncMock(return_value=[APPOINTMENT])
    services["appointments"].cancel_my = AsyncMock(return_value=APPOINTMENT)
    services["reschedule"].list_my_options = AsyncMock(
        return_value=RescheduleAvailability(appointment=APPOINTMENT, windows=[WINDOW])
    )
    services["reschedule"].reschedule_my = AsyncMock(
        return_value=MagicMock(model_dump=MagicMock(return_value={"appointment_id": 11}))
    )
    services["payments"].get_my = AsyncMock(
        return_value=PaymentView(
            id=21,
            business_id=1,
            appointment_id=11,
            provider=PaymentMode.MANUAL,
            provider_payment_id="manual:21",
            amount=Decimal("500.00"),
            refunded_amount=Decimal("0.00"),
            currency="RUB",
            status=PaymentStatus.PENDING,
            payment_type=PaymentType.DEPOSIT,
            confirmation_url=None,
            expires_at=None,
            paid_at=None,
            cancelled_at=None,
            refunded_at=None,
        )
    )

    appointments = await product.dispatch(
        request(method="GET", path="/api/v1/appointments"),
        SESSION,
        correlation_id="request-12345678",
    )
    payment = await product.dispatch(
        request(method="GET", path="/api/v1/payments/21"),
        SESSION,
        correlation_id="request-12345678",
    )
    await product.dispatch(
        request(
            method="POST",
            path="/api/v1/appointments/11/cancel",
            headers={"content-type": "application/json"},
            body=b'{"reason":"plans_changed"}',
        ),
        SESSION,
        correlation_id="request-12345678",
    )
    options = await product.dispatch(
        request(method="GET", path="/api/v1/appointments/11/reschedule-options"),
        SESSION,
        correlation_id="request-12345678",
    )

    assert appointments is not None and payload(appointments.body)["appointments"]
    assert payment is not None and payload(payment.body)["id"] == 21
    assert options is not None and payload(options.body)["windows"][0]["id"] == 7  # type: ignore[index]
    services["appointments"].cancel_my.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_query_json_and_missing_idempotency_fail_before_services() -> None:
    product, services = build_product()

    with pytest.raises(SafeHttpError, match="query_invalid"):
        await product.dispatch(
            request(
                method="GET",
                path="/api/v1/availability/dates",
                query=b"service_id=5&service_id=6",
            ),
            SESSION,
            correlation_id="request-12345678",
        )
    with pytest.raises(SafeHttpError, match="idempotency_key_missing"):
        await product.dispatch(
            request(
                method="POST",
                path="/api/v1/reservations",
                headers={"content-type": "application/json"},
                body=b"{}",
            ),
            SESSION,
            correlation_id="request-12345678",
        )

    services["booking"].list_availability.assert_not_called()
    services["booking"].book.assert_not_called()
