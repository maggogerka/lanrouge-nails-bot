"""SQL shape guards for tenant boundaries, locks and idempotent persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.database.models.commerce import BookingReservation
from app.database.models.payment import PaymentWebhookEvent, Refund
from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus, ReservationStatus
from app.domain.payments import WebhookProcessingStatus
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.reservation_repository import ReservationRepository

NOW = datetime(2026, 8, 10, 10, tzinfo=UTC)


class ScalarRows:
    def __init__(self, value: object = None, rows: list[object] | None = None) -> None:
        self.value = value
        self.rows = rows or []

    def one_or_none(self) -> object:
        return self.value

    def one(self) -> object:
        if self.value is None:
            raise AssertionError("missing scalar row")
        return self.value

    def all(self) -> list[object]:
        return self.rows


def sql(statement: object) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_expiry_claim_is_tenant_scoped_and_skip_locked() -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=ScalarRows(rows=[]))
    repository = ReservationRepository(session, 7)

    await repository.claim_expired(now=NOW, limit=25)

    statement = session.scalars.await_args.args[0]
    compiled = sql(statement)
    assert "booking_reservations.business_id" in compiled
    assert "booking_reservations.status" in compiled
    assert "FOR UPDATE SKIP LOCKED" in compiled


@pytest.mark.asyncio
async def test_reservation_idempotency_lookup_is_locked_and_tenant_scoped() -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=ScalarRows())
    repository = ReservationRepository(session, 7)

    await repository.get_by_idempotency_key("request-12345678", for_update=True)

    compiled = sql(session.scalars.await_args.args[0])
    assert "booking_reservations.business_id" in compiled
    assert "booking_reservations.idempotency_key" in compiled
    assert "FOR UPDATE" in compiled


@pytest.mark.asyncio
async def test_active_reservation_by_appointment_is_locked_and_tenant_scoped() -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=ScalarRows())
    repository = ReservationRepository(session, 7)

    await repository.get_active_for_appointment(31, for_update=True)

    compiled = sql(session.scalars.await_args.args[0])
    assert "booking_reservations.business_id" in compiled
    assert "booking_reservations.appointment_id" in compiled
    assert "booking_reservations.status" in compiled
    assert "FOR UPDATE" in compiled


@pytest.mark.asyncio
async def test_client_booking_lock_is_tenant_scoped_and_for_update() -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=ScalarRows())
    repository = ReservationRepository(session, 7)

    await repository.lock_client_for_booking(31)

    compiled = sql(session.scalars.await_args.args[0])
    assert "business_clients.business_id" in compiled
    assert "business_clients.user_id" in compiled
    assert "FOR UPDATE" in compiled


@pytest.mark.asyncio
async def test_future_quota_query_includes_cancellations_only_when_configured() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=0)
    repository = AppointmentRepository(session, 7)

    await repository.count_for_future_booking_limit(
        client_id=31,
        now=NOW,
        horizon_days=30,
        include_client_cancellations=False,
    )
    without_cancellations = sql(session.scalar.await_args.args[0])
    await repository.count_for_future_booking_limit(
        client_id=31,
        now=NOW,
        horizon_days=30,
        include_client_cancellations=True,
    )
    with_cancellations = sql(session.scalar.await_args.args[0])

    assert "appointments.business_id" in without_cancellations
    assert "appointments.client_id" in without_cancellations
    assert "appointments.scheduled_start_at" in without_cancellations
    assert "appointments.cancelled_at" not in without_cancellations
    assert "appointments.cancelled_at" in with_cancellations


@pytest.mark.asyncio
async def test_payment_pending_refund_sum_is_business_scoped() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=Decimal("12.30"))
    repository = PaymentRepository(session, 7)

    result = await repository.sum_pending_refunds(31)

    compiled = sql(session.scalar.await_args.args[0])
    assert result == Decimal("12.30")
    assert "refunds.business_id" in compiled
    assert "refunds.payment_id" in compiled
    assert "refunds.status" in compiled


@pytest.mark.asyncio
async def test_client_payment_query_is_tenant_and_owner_scoped() -> None:
    session = MagicMock()
    session.execute = AsyncMock(return_value=ScalarRows(rows=[]))
    repository = PaymentRepository(session, 7)

    await repository.list_for_client(31, statuses={PaymentStatus.PENDING}, limit=7, offset=0)

    compiled = sql(session.execute.await_args.args[0])
    assert "payments.business_id" in compiled
    assert "appointments.business_id" in compiled
    assert "appointments.client_id" in compiled
    assert "payments.status" in compiled


@pytest.mark.asyncio
async def test_refund_provider_lookup_is_locked_and_tenant_scoped() -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=ScalarRows())
    repository = PaymentRepository(session, 7)

    await repository.get_refund_by_provider_id(
        PaymentMode.YOOKASSA,
        "provider-refund-1",
        for_update=True,
    )

    compiled = sql(session.scalars.await_args.args[0])
    assert "refunds.business_id" in compiled
    assert "refunds.provider" in compiled
    assert "refunds.provider_refund_id" in compiled
    assert "FOR UPDATE" in compiled


@pytest.mark.asyncio
async def test_refund_insert_uses_atomic_business_idempotency_dedupe() -> None:
    existing = Refund(
        id=51,
        business_id=7,
        payment_id=31,
        provider=PaymentMode.YOOKASSA,
        provider_refund_id=None,
        idempotency_key="refund-1234567890123456",
        amount=Decimal("100.00"),
        currency="RUB",
        status=RefundStatus.PENDING,
        reason_code="requested_by_business",
        safe_metadata={},
        attempts=0,
    )
    draft = Refund(
        business_id=7,
        payment_id=31,
        provider=PaymentMode.YOOKASSA,
        provider_refund_id=None,
        idempotency_key="refund-1234567890123456",
        amount=Decimal("100.00"),
        currency="RUB",
        status=RefundStatus.PENDING,
        reason_code="requested_by_business",
        safe_metadata={},
        attempts=0,
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.scalars = AsyncMock(return_value=ScalarRows(existing))
    repository = PaymentRepository(session, 7)

    stored, inserted = await repository.add_refund_if_absent(draft)

    compiled = sql(session.scalar.await_args.args[0])
    assert stored is existing
    assert not inserted
    assert "ON CONFLICT (business_id, idempotency_key) DO NOTHING" in compiled


@pytest.mark.asyncio
async def test_webhook_insert_uses_atomic_conflict_dedupe() -> None:
    existing = PaymentWebhookEvent(
        id=81,
        business_id=7,
        payment_id=31,
        provider=PaymentMode.YOOKASSA,
        event_key="a" * 64,
        event_type="payment.succeeded",
        provider_object_id="safe-object-ref",
        provider_payment_id="safe-payment-ref",
        payload_sha256="b" * 64,
        status=WebhookProcessingStatus.PENDING,
        received_at=NOW,
        expires_at=NOW + timedelta(days=30),
        attempts=0,
    )
    event = PaymentWebhookEvent(
        business_id=7,
        payment_id=31,
        provider=PaymentMode.YOOKASSA,
        event_key="a" * 64,
        event_type="payment.succeeded",
        provider_object_id="safe-object-ref",
        provider_payment_id="safe-payment-ref",
        payload_sha256="b" * 64,
        status=WebhookProcessingStatus.PENDING,
        received_at=NOW,
        expires_at=NOW + timedelta(days=30),
        attempts=0,
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.scalars = AsyncMock(return_value=ScalarRows(existing))
    repository = PaymentRepository(session, 7)

    stored, inserted = await repository.add_webhook_if_absent(event)

    compiled = sql(session.scalar.await_args.args[0])
    assert stored is existing
    assert not inserted
    assert "ON CONFLICT (business_id, provider, event_key) DO NOTHING" in compiled


@pytest.mark.asyncio
async def test_repository_rejects_cross_business_entity_before_flush() -> None:
    reservation = BookingReservation(
        business_id=8,
        client_id=41,
        staff_member_id=5,
        window_id=11,
        service_id=3,
        token_digest="a" * 64,
        idempotency_key="request-12345678",
        status=ReservationStatus.ACTIVE,
        expires_at=NOW + timedelta(minutes=20),
    )
    session = MagicMock()
    session.flush = AsyncMock()
    repository = ReservationRepository(session, 7)

    with pytest.raises(ValueError, match="another business"):
        await repository.add(reservation)

    session.flush.assert_not_awaited()
