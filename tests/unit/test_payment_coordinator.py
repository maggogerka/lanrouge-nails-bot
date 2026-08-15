"""Transactional payment coordinator tests for replay, tenancy, RBAC, and locking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database.models.appointment import Appointment
from app.database.models.commerce import BookingReservation
from app.database.models.payment import Payment, PaymentWebhookEvent, Refund
from app.domain.enums import (
    AppointmentStatus,
    ManualPaymentStatus,
    PaymentMode,
    PaymentStatus,
    RefundStatus,
    ReservationStatus,
    StaffRole,
)
from app.domain.errors import AuthorizationError, EntityNotFoundError
from app.domain.payments import PaymentStateError, PaymentType, WebhookProcessingStatus
from app.payments.providers.base import (
    PaymentProvider,
    PaymentProviderProtocolError,
    PaymentProviderUnavailableError,
    ProviderPayment,
    ProviderRefund,
    ProviderWebhookEvent,
)
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.payment import RefundCreate
from app.services.payment_coordinator import (
    ManualPaymentApprovalCoordinator,
    PaymentUnitOfWorkFactory,
    RefundCoordinator,
    WebhookProcessingError,
    YooKassaWebhookLifecycleCoordinator,
)
from app.services.payment_service import PaymentService

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)
REFUND_KEY = "refund-1234567890123456"


class FakeUnitOfWork:
    def __init__(self, business_id: int = 7) -> None:
        self.business_id = business_id
        self.payments = MagicMock()
        self.payments.business_id = business_id
        self.appointments = MagicMock()
        self.appointments.get = AsyncMock(return_value=SimpleNamespace(staff_member_id=5))
        self.reservations = MagicMock()
        self.reservations.business_id = business_id
        self.reservations.get_appointment_for_update = AsyncMock(
            return_value=Appointment(
                id=11,
                business_id=business_id,
                status=AppointmentStatus.REFUND_PENDING,
            )
        )
        self.reservations.add_history = AsyncMock()
        self.audit = MagicMock()
        self.audit.business_id = business_id
        self.audit.add = AsyncMock()
        self.commit_count = 0
        self.active = False

    async def __aenter__(self) -> FakeUnitOfWork:
        self.active = True
        return self

    async def __aexit__(self, *args: object) -> None:
        del args
        self.active = False

    async def commit(self) -> None:
        self.commit_count += 1


class UnitOfWorkSequence:
    def __init__(self, *items: FakeUnitOfWork) -> None:
        self._items = list(items)

    def __call__(self) -> FakeUnitOfWork:
        if not self._items:
            raise AssertionError("unexpected unit of work")
        return self._items.pop(0)


def uow_factory(*items: FakeUnitOfWork) -> PaymentUnitOfWorkFactory:
    return cast(PaymentUnitOfWorkFactory, UnitOfWorkSequence(*items))


def staff_context(*, role: StaffRole = StaffRole.OWNER) -> StaffContext:
    return StaffContext(
        business_id=7,
        staff_member_id=5,
        user_id=41,
        telegram_id=700_001,
        display_name="Owner",
        role=role,
        is_bookable=False,
    )


def payment(
    *,
    provider: PaymentMode = PaymentMode.YOOKASSA,
    status: PaymentStatus = PaymentStatus.PENDING,
    currency: str = "RUB",
) -> Payment:
    result = Payment(
        id=31,
        business_id=7,
        appointment_id=11,
        provider=provider,
        provider_payment_id="provider-payment-31",
        idempotency_key="payment-123456789012345",
        amount=Decimal("500.00"),
        refunded_amount=Decimal("0.00"),
        currency=currency,
        status=status,
        payment_type=PaymentType.DEPOSIT,
        safe_metadata={"business_id": "7", "appointment_id": "11"},
        attempts=0,
    )
    if provider is PaymentMode.MANUAL:
        result.manual_status = ManualPaymentStatus.REVIEW_PENDING
    return result


def reservation() -> BookingReservation:
    return BookingReservation(
        id=61,
        business_id=7,
        client_id=101,
        staff_member_id=5,
        window_id=19,
        service_id=3,
        appointment_id=11,
        token_digest="a" * 64,
        idempotency_key="reservation-1234567890",
        status=ReservationStatus.ACTIVE,
        expires_at=NOW + timedelta(minutes=20),
    )


def webhook_event(*, provider: PaymentMode = PaymentMode.YOOKASSA) -> ProviderWebhookEvent:
    return ProviderWebhookEvent(
        provider=provider,
        event_key="a" * 64,
        event_type="payment.succeeded",
        provider_object_id="provider-payment-31",
        provider_payment_id="provider-payment-31",
        payload_sha256="a" * 64,
    )


def webhook_row(*, status: WebhookProcessingStatus) -> PaymentWebhookEvent:
    return PaymentWebhookEvent(
        id=81,
        business_id=7,
        payment_id=31 if status is WebhookProcessingStatus.PROCESSED else None,
        provider=PaymentMode.YOOKASSA,
        event_key="a" * 64,
        event_type="payment.succeeded",
        provider_object_id="provider-payment-31",
        provider_payment_id="provider-payment-31",
        payload_sha256="a" * 64,
        status=status,
        received_at=NOW,
        processed_at=NOW if status is not WebhookProcessingStatus.PENDING else None,
        expires_at=NOW + timedelta(days=30),
        attempts=1,
    )


def provider_service(
    *,
    mode: PaymentMode,
    payment_result: ProviderPayment | None = None,
    refund_result: ProviderRefund | None = None,
) -> tuple[PaymentService, MagicMock]:
    provider = MagicMock()
    provider.mode = mode
    provider.supports_partial_refunds = True
    provider.get_payment = AsyncMock(return_value=payment_result)
    provider.get_refund = AsyncMock()
    provider.refund_payment = AsyncMock(return_value=refund_result)
    return PaymentService(cast(PaymentProvider, provider)), provider


def authorize(actor: StaffContext) -> MagicMock:
    service = MagicMock()
    service.authorize = AsyncMock(return_value=actor)
    return service


@pytest.mark.asyncio
async def test_webhook_lifecycle_locks_payment_refetches_and_consumes_reservation() -> None:
    local_payment = payment()
    authoritative = ProviderPayment(
        provider=PaymentMode.YOOKASSA,
        provider_payment_id="provider-payment-31",
        status=PaymentStatus.SUCCEEDED,
        amount=Decimal("500.00"),
        currency="RUB",
        safe_metadata=local_payment.safe_metadata,
        paid_at=NOW,
    )
    service, provider = provider_service(
        mode=PaymentMode.YOOKASSA,
        payment_result=authoritative,
    )
    inbox_uow = FakeUnitOfWork()
    captured: list[PaymentWebhookEvent] = []

    async def insert_event(event: PaymentWebhookEvent) -> tuple[PaymentWebhookEvent, bool]:
        event.id = 81
        captured.append(event)
        return event, True

    inbox_uow.payments.add_webhook_if_absent = AsyncMock(side_effect=insert_event)
    inbox_uow.payments.get_by_provider_id = AsyncMock(return_value=local_payment)
    apply_uow = FakeUnitOfWork()
    apply_uow.payments.get_webhook_by_event_key = AsyncMock(
        side_effect=lambda *_args, **_kwargs: captured[0]
    )
    apply_uow.payments.get_by_provider_id = AsyncMock(return_value=local_payment)
    reconcile_uow = FakeUnitOfWork()
    reconcile_uow.payments.get = AsyncMock(return_value=local_payment)
    reconcile_uow.reservations.get_active_for_appointment = AsyncMock(return_value=reservation())

    async def fetch_without_transaction(_provider_payment_id: str) -> ProviderPayment:
        assert not inbox_uow.active
        assert not apply_uow.active
        return authoritative

    provider.get_payment.side_effect = fetch_without_transaction
    coordinator = YooKassaWebhookLifecycleCoordinator(
        uow_factory(inbox_uow, apply_uow, reconcile_uow),
        service,
        business_id=7,
    )

    with patch(
        "app.services.payment_coordinator.ReservationService.consume",
        new=AsyncMock(return_value=reservation()),
    ) as consume:
        outcome = await coordinator.process_untrusted_notification(
            webhook_event(),
            correlation_id="request-123",
            now=NOW,
        )

    assert not outcome.duplicate
    assert local_payment.status is PaymentStatus.SUCCEEDED
    assert captured[0].status is WebhookProcessingStatus.PROCESSED
    provider.get_payment.assert_awaited_once_with("provider-payment-31")
    assert not inbox_uow.active
    assert not apply_uow.active
    inbox_uow.payments.get_by_provider_id.assert_awaited_once_with(
        PaymentMode.YOOKASSA,
        "provider-payment-31",
        for_update=True,
    )
    reconcile_uow.reservations.get_active_for_appointment.assert_awaited_once_with(
        11,
        for_update=True,
    )
    consume.assert_awaited_once()
    assert inbox_uow.commit_count == 1
    assert apply_uow.commit_count == 1
    assert reconcile_uow.commit_count == 1


@pytest.mark.asyncio
async def test_processed_webhook_replay_does_not_refetch_but_repairs_reservation() -> None:
    local_payment = payment(status=PaymentStatus.SUCCEEDED)
    service, provider = provider_service(mode=PaymentMode.YOOKASSA)
    inbox_uow = FakeUnitOfWork()
    stored = webhook_row(status=WebhookProcessingStatus.PROCESSED)
    inbox_uow.payments.add_webhook_if_absent = AsyncMock(return_value=(stored, False))
    inbox_uow.payments.get_webhook_by_event_key = AsyncMock(return_value=stored)
    reconcile_uow = FakeUnitOfWork()
    reconcile_uow.payments.get = AsyncMock(return_value=local_payment)
    reconcile_uow.reservations.get_active_for_appointment = AsyncMock(return_value=None)
    coordinator = YooKassaWebhookLifecycleCoordinator(
        uow_factory(inbox_uow, reconcile_uow),
        service,
        business_id=7,
    )

    outcome = await coordinator.process_untrusted_notification(
        webhook_event(),
        correlation_id="request-123",
        now=NOW,
    )

    assert outcome.duplicate
    provider.get_payment.assert_not_awaited()
    inbox_uow.payments.get_webhook_by_event_key.assert_awaited_once_with(
        PaymentMode.YOOKASSA,
        "a" * 64,
        for_update=True,
    )
    reconcile_uow.reservations.get_active_for_appointment.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("amount", "currency"),
    [(Decimal("499.00"), "RUB"), (Decimal("500.00"), "USD")],
)
async def test_webhook_wrong_authoritative_money_is_terminal_and_never_consumed(
    amount: Decimal,
    currency: str,
) -> None:
    local_payment = payment()
    authoritative = ProviderPayment(
        provider=PaymentMode.YOOKASSA,
        provider_payment_id="provider-payment-31",
        status=PaymentStatus.SUCCEEDED,
        amount=amount,
        currency=currency,
        safe_metadata=local_payment.safe_metadata,
    )
    service, _ = provider_service(mode=PaymentMode.YOOKASSA, payment_result=authoritative)
    inbox_uow = FakeUnitOfWork()
    captured: list[PaymentWebhookEvent] = []

    async def insert_event(event: PaymentWebhookEvent) -> tuple[PaymentWebhookEvent, bool]:
        event.id = 81
        captured.append(event)
        return event, True

    inbox_uow.payments.add_webhook_if_absent = AsyncMock(side_effect=insert_event)
    inbox_uow.payments.get_by_provider_id = AsyncMock(return_value=local_payment)
    apply_uow = FakeUnitOfWork()
    apply_uow.payments.get_webhook_by_event_key = AsyncMock(
        side_effect=lambda *_args, **_kwargs: captured[0]
    )
    apply_uow.payments.get_by_provider_id = AsyncMock(return_value=local_payment)
    coordinator = YooKassaWebhookLifecycleCoordinator(
        uow_factory(inbox_uow, apply_uow),
        service,
        business_id=7,
    )

    with pytest.raises(WebhookProcessingError) as raised:
        await coordinator.process_untrusted_notification(
            webhook_event(),
            correlation_id="request-123",
            now=NOW,
        )

    assert not raised.value.retryable
    assert local_payment.status is PaymentStatus.PENDING
    assert captured[0].status is WebhookProcessingStatus.FAILED
    assert inbox_uow.commit_count == 1
    assert apply_uow.commit_count == 1


@pytest.mark.asyncio
async def test_webhook_tenant_lookup_miss_stays_pending_for_safe_retry() -> None:
    service, provider = provider_service(mode=PaymentMode.YOOKASSA)
    inbox_uow = FakeUnitOfWork()
    captured: list[PaymentWebhookEvent] = []

    async def insert_event(event: PaymentWebhookEvent) -> tuple[PaymentWebhookEvent, bool]:
        event.id = 81
        captured.append(event)
        return event, True

    inbox_uow.payments.add_webhook_if_absent = AsyncMock(side_effect=insert_event)
    inbox_uow.payments.get_by_provider_id = AsyncMock(return_value=None)
    coordinator = YooKassaWebhookLifecycleCoordinator(
        uow_factory(inbox_uow),
        service,
        business_id=7,
    )

    with pytest.raises(WebhookProcessingError) as raised:
        await coordinator.process_untrusted_notification(
            webhook_event(),
            correlation_id="request-123",
            now=NOW,
        )

    assert raised.value.retryable
    assert captured[0].status is WebhookProcessingStatus.PENDING
    assert captured[0].last_error_code == "payment_not_found"
    assert captured[0].attempts == 1
    provider.get_payment.assert_not_awaited()
    assert inbox_uow.commit_count == 1


@pytest.mark.asyncio
async def test_webhook_provider_failure_is_persisted_after_transaction_closes() -> None:
    local_payment = payment()
    service, provider = provider_service(mode=PaymentMode.YOOKASSA)
    inbox_uow = FakeUnitOfWork()
    failure_uow = FakeUnitOfWork()
    captured: list[PaymentWebhookEvent] = []

    async def insert_event(event: PaymentWebhookEvent) -> tuple[PaymentWebhookEvent, bool]:
        event.id = 81
        captured.append(event)
        return event, True

    async def fail_without_transaction(_provider_payment_id: str) -> ProviderPayment:
        assert not inbox_uow.active
        assert not failure_uow.active
        raise PaymentProviderUnavailableError("provider_timeout")

    inbox_uow.payments.add_webhook_if_absent = AsyncMock(side_effect=insert_event)
    inbox_uow.payments.get_by_provider_id = AsyncMock(return_value=local_payment)
    failure_uow.payments.get_webhook_by_event_key = AsyncMock(
        side_effect=lambda *_args, **_kwargs: captured[0]
    )
    provider.get_payment.side_effect = fail_without_transaction
    coordinator = YooKassaWebhookLifecycleCoordinator(
        uow_factory(inbox_uow, failure_uow),
        service,
        business_id=7,
    )

    with pytest.raises(WebhookProcessingError) as raised:
        await coordinator.process_untrusted_notification(
            webhook_event(),
            correlation_id="request-123",
            now=NOW,
        )

    assert raised.value.retryable
    assert captured[0].status is WebhookProcessingStatus.PENDING
    assert captured[0].last_error_code == "provider_timeout"
    assert inbox_uow.commit_count == 1
    assert failure_uow.commit_count == 1


@pytest.mark.asyncio
async def test_webhook_rejects_wrong_provider_before_opening_transaction() -> None:
    service, _ = provider_service(mode=PaymentMode.YOOKASSA)
    coordinator = YooKassaWebhookLifecycleCoordinator(
        uow_factory(),
        service,
        business_id=7,
    )

    with pytest.raises(WebhookProcessingError) as raised:
        await coordinator.process_untrusted_notification(
            webhook_event(provider=PaymentMode.MANUAL),
            correlation_id="request-123",
            now=NOW,
        )

    assert not raised.value.retryable


@pytest.mark.asyncio
async def test_manual_approval_reauthorizes_locks_audits_and_consumes() -> None:
    actor = staff_context()
    auth = authorize(actor)
    local_payment = payment(provider=PaymentMode.MANUAL)
    service, _ = provider_service(mode=PaymentMode.MANUAL)
    approval_uow = FakeUnitOfWork()
    approval_uow.payments.get = AsyncMock(return_value=local_payment)
    reconcile_uow = FakeUnitOfWork()
    reconcile_uow.payments.get = AsyncMock(return_value=local_payment)
    reconcile_uow.reservations.get_active_for_appointment = AsyncMock(return_value=reservation())
    coordinator = ManualPaymentApprovalCoordinator(
        uow_factory(approval_uow, reconcile_uow),
        auth,
        service,
    )

    with patch(
        "app.services.payment_coordinator.ReservationService.consume",
        new=AsyncMock(return_value=reservation()),
    ) as consume:
        result = await coordinator.approve(
            actor,
            31,
            now=NOW,
            correlation_id="manual-approval-1",
        )

    assert result.status is PaymentStatus.SUCCEEDED
    auth.authorize.assert_awaited_once_with(
        business_id=7,
        telegram_id=actor.telegram_id,
        permission=StaffPermission.APPROVE_PREPAYMENTS,
    )
    approval_uow.payments.get.assert_awaited_once_with(31, for_update=True)
    approval_uow.audit.add.assert_awaited_once()
    consume.assert_awaited_once()


@pytest.mark.asyncio
async def test_master_cannot_approve_another_specialists_payment() -> None:
    actor = staff_context(role=StaffRole.MASTER)
    auth = authorize(actor)
    local_payment = payment(provider=PaymentMode.MANUAL)
    service, _ = provider_service(mode=PaymentMode.MANUAL)
    approval_uow = FakeUnitOfWork()
    approval_uow.payments.get = AsyncMock(return_value=local_payment)
    approval_uow.appointments.get = AsyncMock(
        return_value=SimpleNamespace(staff_member_id=actor.staff_member_id + 1)
    )
    coordinator = ManualPaymentApprovalCoordinator(
        uow_factory(approval_uow),
        auth,
        service,
    )

    with pytest.raises(EntityNotFoundError, match="payment not found"):
        await coordinator.approve(actor, local_payment.id, now=NOW)

    assert local_payment.status is PaymentStatus.PENDING
    approval_uow.audit.add.assert_not_awaited()
    assert approval_uow.commit_count == 0


@pytest.mark.asyncio
async def test_manual_approval_stops_at_live_permission_boundary() -> None:
    actor = staff_context(role=StaffRole.RECEPTIONIST)
    auth = MagicMock()
    auth.authorize = AsyncMock(side_effect=AuthorizationError("not permitted"))
    service, _ = provider_service(mode=PaymentMode.MANUAL)
    coordinator = ManualPaymentApprovalCoordinator(uow_factory(), auth, service)

    with pytest.raises(AuthorizationError):
        await coordinator.approve(actor, 31, now=NOW)

    auth.authorize.assert_awaited_once()


@pytest.mark.asyncio
async def test_refund_locks_payment_reserves_pending_sum_and_submits_once() -> None:
    actor = staff_context()
    auth = authorize(actor)
    local_payment = payment(status=PaymentStatus.SUCCEEDED)
    provider_result = ProviderRefund(
        provider=PaymentMode.YOOKASSA,
        provider_refund_id="provider-refund-1",
        provider_payment_id="provider-payment-31",
        status=RefundStatus.SUCCEEDED,
        amount=Decimal("300.00"),
        currency="RUB",
    )
    service, provider = provider_service(
        mode=PaymentMode.YOOKASSA,
        refund_result=provider_result,
    )
    create_uow = FakeUnitOfWork()
    create_uow.payments.get = AsyncMock(return_value=local_payment)
    create_uow.payments.get_refund_by_idempotency_key = AsyncMock(return_value=None)
    create_uow.payments.sum_pending_refunds = AsyncMock(return_value=Decimal("0.00"))
    captured: list[Refund] = []

    async def add_refund(refund: Refund) -> tuple[Refund, bool]:
        refund.id = 51
        captured.append(refund)
        return refund, True

    create_uow.payments.add_refund_if_absent = AsyncMock(side_effect=add_refund)
    submit_uow = FakeUnitOfWork()
    submit_uow.payments.get = AsyncMock(return_value=local_payment)
    submit_uow.payments.get_refund = AsyncMock(side_effect=lambda *_args, **_kwargs: captured[0])
    apply_uow = FakeUnitOfWork()
    apply_uow.payments.get = AsyncMock(return_value=local_payment)
    apply_uow.payments.get_refund = AsyncMock(side_effect=lambda *_args, **_kwargs: captured[0])

    async def submit_without_transaction(_command: object) -> ProviderRefund:
        assert not create_uow.active
        assert not submit_uow.active
        assert not apply_uow.active
        return provider_result

    provider.refund_payment.side_effect = submit_without_transaction
    coordinator = RefundCoordinator(
        uow_factory(create_uow, submit_uow, apply_uow),
        auth,
        service,
    )
    values = RefundCreate(
        business_id=7,
        payment_id=31,
        amount=Decimal("300.00"),
        idempotency_key=REFUND_KEY,
        correlation_id="refund-request-1",
    )

    outcome = await coordinator.create_and_submit(actor, values, now=NOW)

    assert outcome.created
    assert outcome.refund.status is RefundStatus.SUCCEEDED
    assert outcome.refund.amount == Decimal("300.00")
    assert local_payment.refunded_amount == Decimal("300.00")
    appointment = await apply_uow.reservations.get_appointment_for_update(11)
    assert appointment.status is AppointmentStatus.PARTIALLY_REFUNDED
    apply_uow.reservations.add_history.assert_awaited_once()
    create_uow.payments.get.assert_awaited_once_with(31, for_update=True)
    create_uow.payments.sum_pending_refunds.assert_awaited_once_with(31)
    submit_uow.payments.get.assert_awaited_once_with(31, for_update=True)
    submit_uow.payments.get_refund.assert_awaited_once_with(51, for_update=True)
    apply_uow.payments.get.assert_awaited_once_with(31, for_update=True)
    apply_uow.payments.get_refund.assert_awaited_once_with(51, for_update=True)
    provider.refund_payment.assert_awaited_once()
    assert create_uow.commit_count == 1
    assert submit_uow.commit_count == 1
    assert apply_uow.commit_count == 1


@pytest.mark.asyncio
async def test_refund_replay_with_same_key_never_resubmits_provider() -> None:
    actor = staff_context()
    auth = authorize(actor)
    local_payment = payment(status=PaymentStatus.REFUNDED)
    local_payment.refunded_amount = Decimal("500.00")
    existing = Refund(
        id=51,
        business_id=7,
        payment_id=31,
        provider=PaymentMode.YOOKASSA,
        provider_refund_id="provider-refund-1",
        idempotency_key=REFUND_KEY,
        amount=Decimal("500.00"),
        currency="RUB",
        status=RefundStatus.SUCCEEDED,
        reason_code="requested_by_business",
        safe_metadata={},
        requested_by_user_id=41,
        attempts=1,
        succeeded_at=NOW,
    )
    service, provider = provider_service(mode=PaymentMode.YOOKASSA)
    lookup_uow = FakeUnitOfWork()
    lookup_uow.payments.get = AsyncMock(return_value=local_payment)
    lookup_uow.payments.get_refund_by_idempotency_key = AsyncMock(return_value=existing)
    submit_uow = FakeUnitOfWork()
    submit_uow.payments.get = AsyncMock(return_value=local_payment)
    submit_uow.payments.get_refund = AsyncMock(return_value=existing)
    coordinator = RefundCoordinator(
        uow_factory(lookup_uow, submit_uow),
        auth,
        service,
    )
    values = RefundCreate(
        business_id=7,
        payment_id=31,
        amount=Decimal("500.00"),
        idempotency_key=REFUND_KEY,
    )

    outcome = await coordinator.create_and_submit(actor, values, now=NOW)

    assert not outcome.created
    assert outcome.refund.status is RefundStatus.SUCCEEDED
    provider.refund_payment.assert_not_awaited()
    lookup_uow.payments.add_refund_if_absent.assert_not_called()


@pytest.mark.asyncio
async def test_provider_refund_amount_mismatch_fails_and_restores_payment() -> None:
    actor = staff_context()
    auth = authorize(actor)
    local_payment = payment(status=PaymentStatus.SUCCEEDED)
    malicious_result = ProviderRefund(
        provider=PaymentMode.YOOKASSA,
        provider_refund_id="provider-refund-1",
        provider_payment_id="provider-payment-31",
        status=RefundStatus.SUCCEEDED,
        amount=Decimal("99.00"),
        currency="RUB",
    )
    service, _ = provider_service(
        mode=PaymentMode.YOOKASSA,
        refund_result=malicious_result,
    )
    create_uow = FakeUnitOfWork()
    create_uow.payments.get = AsyncMock(return_value=local_payment)
    create_uow.payments.get_refund_by_idempotency_key = AsyncMock(return_value=None)
    create_uow.payments.sum_pending_refunds = AsyncMock(return_value=Decimal("0.00"))
    captured: list[Refund] = []

    async def insert_refund(refund: Refund) -> tuple[Refund, bool]:
        refund.id = 51
        captured.append(refund)
        return refund, True

    create_uow.payments.add_refund_if_absent = AsyncMock(side_effect=insert_refund)
    submit_uow = FakeUnitOfWork()
    submit_uow.payments.get = AsyncMock(return_value=local_payment)
    submit_uow.payments.get_refund = AsyncMock(side_effect=lambda *_args, **_kwargs: captured[0])
    apply_uow = FakeUnitOfWork()
    apply_uow.payments.get = AsyncMock(return_value=local_payment)
    apply_uow.payments.get_refund = AsyncMock(side_effect=lambda *_args, **_kwargs: captured[0])
    coordinator = RefundCoordinator(
        uow_factory(create_uow, submit_uow, apply_uow),
        auth,
        service,
    )
    values = RefundCreate(
        business_id=7,
        payment_id=31,
        amount=Decimal("100.00"),
        idempotency_key=REFUND_KEY,
    )

    with pytest.raises(PaymentProviderProtocolError, match="money_mismatch"):
        await coordinator.create_and_submit(actor, values, now=NOW)

    assert captured[0].status is RefundStatus.FAILED
    assert captured[0].last_error_code == "provider_refund_money_mismatch"
    assert local_payment.status is PaymentStatus.SUCCEEDED
    assert local_payment.refunded_amount == Decimal("0.00")
    assert submit_uow.commit_count == 1
    assert apply_uow.commit_count == 1
    apply_uow.audit.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_refund_wrong_currency_is_rejected_before_insert_or_provider_call() -> None:
    actor = staff_context()
    auth = authorize(actor)
    local_payment = payment(status=PaymentStatus.SUCCEEDED)
    service, provider = provider_service(mode=PaymentMode.YOOKASSA)
    create_uow = FakeUnitOfWork()
    create_uow.payments.get = AsyncMock(return_value=local_payment)
    create_uow.payments.get_refund_by_idempotency_key = AsyncMock(return_value=None)
    create_uow.payments.sum_pending_refunds = AsyncMock(return_value=Decimal("0.00"))
    coordinator = RefundCoordinator(uow_factory(create_uow), auth, service)
    values = RefundCreate(
        business_id=7,
        payment_id=31,
        amount=Decimal("100.00"),
        currency="USD",
        idempotency_key=REFUND_KEY,
    )

    with pytest.raises(PaymentStateError, match="currency"):
        await coordinator.create_and_submit(actor, values, now=NOW)

    create_uow.payments.add_refund_if_absent.assert_not_called()
    provider.refund_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_refund_pending_sum_prevents_concurrent_over_refund() -> None:
    actor = staff_context()
    auth = authorize(actor)
    local_payment = payment(status=PaymentStatus.PARTIALLY_REFUNDED)
    local_payment.refunded_amount = Decimal("100.00")
    service, provider = provider_service(mode=PaymentMode.YOOKASSA)
    create_uow = FakeUnitOfWork()
    create_uow.payments.get = AsyncMock(return_value=local_payment)
    create_uow.payments.get_refund_by_idempotency_key = AsyncMock(return_value=None)
    create_uow.payments.sum_pending_refunds = AsyncMock(return_value=Decimal("100.00"))
    coordinator = RefundCoordinator(uow_factory(create_uow), auth, service)
    values = RefundCreate(
        business_id=7,
        payment_id=31,
        amount=Decimal("301.00"),
        idempotency_key=REFUND_KEY,
    )

    with pytest.raises(PaymentStateError):
        await coordinator.create_and_submit(actor, values, now=NOW)

    create_uow.payments.get.assert_awaited_once_with(31, for_update=True)
    create_uow.payments.sum_pending_refunds.assert_awaited_once_with(31)
    create_uow.payments.add_refund_if_absent.assert_not_called()
    provider.refund_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_refund_idempotency_race_restores_local_payment_state() -> None:
    actor = staff_context()
    auth = authorize(actor)
    local_payment = payment(status=PaymentStatus.SUCCEEDED)
    conflicting = Refund(
        id=52,
        business_id=7,
        payment_id=999,
        provider=PaymentMode.YOOKASSA,
        provider_refund_id=None,
        idempotency_key=REFUND_KEY,
        amount=Decimal("100.00"),
        currency="RUB",
        status=RefundStatus.PENDING,
        reason_code="requested_by_business",
        safe_metadata={},
        requested_by_user_id=41,
        attempts=0,
    )
    service, provider = provider_service(mode=PaymentMode.YOOKASSA)
    create_uow = FakeUnitOfWork()
    create_uow.payments.get = AsyncMock(return_value=local_payment)
    create_uow.payments.get_refund_by_idempotency_key = AsyncMock(return_value=None)
    create_uow.payments.sum_pending_refunds = AsyncMock(return_value=Decimal("0.00"))
    create_uow.payments.add_refund_if_absent = AsyncMock(return_value=(conflicting, False))
    coordinator = RefundCoordinator(uow_factory(create_uow), auth, service)
    values = RefundCreate(
        business_id=7,
        payment_id=31,
        amount=Decimal("100.00"),
        idempotency_key=REFUND_KEY,
    )

    with pytest.raises(PaymentStateError, match="idempotency"):
        await coordinator.create_and_submit(actor, values, now=NOW)

    assert local_payment.status is PaymentStatus.SUCCEEDED
    assert create_uow.commit_count == 0
    provider.refund_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_refund_approval_reauthorizes_and_audits() -> None:
    actor = staff_context()
    auth = authorize(actor)
    local_payment = payment(
        provider=PaymentMode.MANUAL,
        status=PaymentStatus.REFUND_PENDING,
    )
    local_refund = Refund(
        id=51,
        business_id=7,
        payment_id=31,
        provider=PaymentMode.MANUAL,
        provider_refund_id="manual_refund_123",
        idempotency_key=REFUND_KEY,
        amount=Decimal("100.00"),
        currency="RUB",
        status=RefundStatus.PENDING,
        reason_code="requested_by_business",
        safe_metadata={},
        requested_by_user_id=41,
        attempts=1,
    )
    service, _ = provider_service(mode=PaymentMode.MANUAL)
    approval_uow = FakeUnitOfWork()
    approval_uow.payments.get_refund = AsyncMock(side_effect=[local_refund, local_refund])
    approval_uow.payments.get = AsyncMock(return_value=local_payment)
    coordinator = RefundCoordinator(uow_factory(approval_uow), auth, service)

    result = await coordinator.approve_manual_refund(
        actor,
        51,
        now=NOW,
        correlation_id="manual-refund-1",
    )

    assert result.status is RefundStatus.SUCCEEDED
    assert local_payment.status is PaymentStatus.PARTIALLY_REFUNDED
    assert local_payment.refunded_amount == Decimal("100.00")
    auth.authorize.assert_awaited_once_with(
        business_id=7,
        telegram_id=actor.telegram_id,
        permission=StaffPermission.REFUND_PAYMENTS,
    )
    approval_uow.payments.get.assert_awaited_once_with(31, for_update=True)
    assert approval_uow.payments.get_refund.await_args_list[1].kwargs == {"for_update": True}
    approval_uow.audit.add.assert_awaited_once()
    assert approval_uow.commit_count == 1


@pytest.mark.asyncio
async def test_refund_request_cannot_spoof_authorized_actor() -> None:
    actor = staff_context()
    auth = authorize(actor)
    service, _ = provider_service(mode=PaymentMode.YOOKASSA)
    coordinator = RefundCoordinator(uow_factory(), auth, service)
    values = RefundCreate(
        business_id=7,
        payment_id=31,
        amount=Decimal("100.00"),
        idempotency_key=REFUND_KEY,
        requested_by_user_id=999,
    )

    with pytest.raises(PaymentStateError, match="actor"):
        await coordinator.create_and_submit(actor, values, now=NOW)
