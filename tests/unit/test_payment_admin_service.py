"""Live authorization and safe projections for the Telegram payment panel."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models.payment import Payment
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    ManualPaymentStatus,
    PaymentMode,
    PaymentStatus,
    ReservationStatus,
    StaffRole,
)
from app.domain.payments import PaymentType
from app.keyboards.admin.payments import (
    payment_admin_details_keyboard,
    payment_admin_home_keyboard,
    payment_admin_list_keyboard,
    payments_keyboard,
)
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.payment import PaymentAdminSection
from app.services.payment_admin_service import PaymentAdministrationService


def actor(role: StaffRole = StaffRole.OWNER) -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=2,
        user_id=3,
        telegram_id=4,
        display_name="Сотрудник",
        role=role,
        is_bookable=False,
    )


def payment() -> Payment:
    return Payment(
        id=10,
        business_id=1,
        appointment_id=20,
        provider=PaymentMode.MANUAL,
        provider_payment_id="manual_payment_safe",
        idempotency_key="payment-idempotency-0001",
        amount=Decimal("500.00"),
        refunded_amount=Decimal("0.00"),
        currency="RUB",
        status=PaymentStatus.PENDING,
        payment_type=PaymentType.DEPOSIT,
        safe_metadata={"business_id": "1", "appointment_id": "20"},
        attempts=1,
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
        manual_status=ManualPaymentStatus.REVIEW_PENDING,
    )


def payment_context() -> tuple[SimpleNamespace, SimpleNamespace]:
    appointment = SimpleNamespace(
        scheduled_start_at=datetime(2026, 8, 27, 10, tzinfo=UTC),
        service_name_snapshot="Маникюр",
        master_name_snapshot="Руслана",
        staff_member_id=2,
    )
    client = SimpleNamespace(
        first_name="Анна",
        phone="+79990000000",
        username=None,
        telegram_id=123456,
    )
    return appointment, client


@pytest.mark.asyncio
async def test_list_recent_reauthorizes_and_returns_safe_views() -> None:
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=actor())
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.payments.list_recent = AsyncMock(return_value=[payment()])
    service = PaymentAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
        MagicMock(),
    )

    result = await service.list_recent(actor())

    authorization.authorize.assert_awaited_once_with(
        business_id=1,
        telegram_id=4,
        permission=StaffPermission.VIEW_PREPAYMENTS,
    )
    assert result[0].id == 10
    assert "idempotency_key" not in result[0].model_dump()
    assert "safe_metadata" not in result[0].model_dump()


@pytest.mark.asyncio
async def test_master_payment_list_is_repository_scoped_to_self() -> None:
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=actor(StaffRole.MASTER))
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.payments.list_recent = AsyncMock(return_value=[])
    service = PaymentAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
        MagicMock(),
    )

    await service.list_recent(actor(StaffRole.MASTER))

    assert unit_of_work.payments.list_recent.await_args.kwargs["staff_member_id"] == 2


@pytest.mark.asyncio
async def test_active_payment_panel_contains_appointment_and_client_context() -> None:
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=actor())
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    appointment, client = payment_context()
    unit_of_work.settings.get = AsyncMock(return_value=SimpleNamespace(timezone="Europe/Moscow"))
    unit_of_work.payments.list_recent_with_context = AsyncMock(
        return_value=[(payment(), appointment, client)]
    )
    service = PaymentAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
        MagicMock(),
    )

    result = await service.list_panel(actor(), PaymentAdminSection.ACTIVE)

    assert result[0].appointment_start_at == appointment.scheduled_start_at
    assert result[0].client_name == "Анна"
    assert result[0].client_phone == "+79990000000"
    assert result[0].client_telegram_id == 123456
    assert (
        PaymentStatus.PENDING
        in unit_of_work.payments.list_recent_with_context.await_args.kwargs["statuses"]
    )


@pytest.mark.asyncio
async def test_master_context_payment_lookup_is_scoped_to_self() -> None:
    master = actor(StaffRole.MASTER)
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=master)
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    appointment, client = payment_context()
    unit_of_work.settings.get = AsyncMock(return_value=SimpleNamespace(timezone="Europe/Moscow"))
    unit_of_work.payments.get_with_context = AsyncMock(
        return_value=(payment(), appointment, client)
    )
    service = PaymentAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
        MagicMock(),
    )

    result = await service.get_panel_payment(master, 10)

    assert result.id == 10
    unit_of_work.payments.get_with_context.assert_awaited_once_with(10, staff_member_id=2)


@pytest.mark.asyncio
async def test_payment_panel_keyboards_split_sections_and_avoid_private_user_url() -> None:
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=actor())
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    appointment, client = payment_context()
    unit_of_work.settings.get = AsyncMock(return_value=SimpleNamespace(timezone="Europe/Moscow"))
    unit_of_work.payments.list_recent_with_context = AsyncMock(
        return_value=[(payment(), appointment, client)]
    )
    service = PaymentAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
        MagicMock(),
    )
    view = (await service.list_panel(actor(), PaymentAdminSection.ACTIVE))[0]

    home = payment_admin_home_keyboard(active_count=1, history_count=2, can_configure=True)
    home_callbacks = {
        button.callback_data
        for row in home.inline_keyboard
        for button in row
        if button.callback_data
    }
    assert any("active" in value for value in home_callbacks)
    assert any("history" in value for value in home_callbacks)
    assert any("settings" in value for value in home_callbacks)

    listing = payment_admin_list_keyboard((view,), PaymentAdminSection.ACTIVE)
    assert "27.08 13:00" in listing.inline_keyboard[0][0].text
    assert "Анна" in listing.inline_keyboard[0][0].text

    details = payment_admin_details_keyboard(
        view,
        section=PaymentAdminSection.ACTIVE,
        can_manage=True,
        can_reject=True,
        can_refund=True,
    )
    assert all(button.url is None for row in details.inline_keyboard for button in row)
    callbacks = [button.callback_data or "" for row in details.inline_keyboard for button in row]
    assert any("message_client" in value for value in callbacks)
    assert any("approve_prompt" in value for value in callbacks)
    assert any("reject_prompt" in value for value in callbacks)


def test_receptionist_payment_panel_never_offers_manual_approval() -> None:
    payment_view = MagicMock()
    payment_view.id = 10
    payment_view.amount = Decimal("500.00")
    payment_view.currency = "RUB"
    payment_view.status = PaymentStatus.PENDING
    payment_view.provider = PaymentMode.MANUAL

    keyboard = payments_keyboard((payment_view,), can_manage=False)

    assert all(
        "approve" not in (button.callback_data or "")
        for row in keyboard.inline_keyboard
        for button in row
    )


@pytest.mark.asyncio
async def test_manual_rejection_is_atomic_and_replay_safe() -> None:
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=actor())
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    local_payment = payment()
    appointment = SimpleNamespace(
        id=20,
        staff_member_id=2,
        client_id=30,
        status=AppointmentStatus.PENDING_MANUAL_CONFIRMATION,
        reservation_expires_at=None,
        cancelled_at=None,
        cancellation_reason=None,
    )
    reservation = SimpleNamespace(
        status=ReservationStatus.AWAITING_REVIEW,
        window_id=40,
        cancelled_at=None,
    )
    window = SimpleNamespace(status=AvailabilityWindowStatus.RESERVED)
    client = SimpleNamespace(telegram_id=50)
    unit_of_work.payments.get = AsyncMock(return_value=local_payment)
    unit_of_work.appointments.get = AsyncMock(return_value=appointment)
    unit_of_work.appointments.add_history = AsyncMock()
    unit_of_work.reservations.get_active_for_appointment = AsyncMock(return_value=reservation)
    unit_of_work.reservations.get_window_for_update = AsyncMock(return_value=window)
    unit_of_work.users.get_by_id = AsyncMock(return_value=client)
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    service = PaymentAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
        MagicMock(),
    )

    first = await service.reject_manual(actor(), 10, reason="  Не найден перевод  ")
    replay = await service.reject_manual(actor(), 10, reason="другое значение")

    assert first.changed is True
    assert replay.changed is False
    assert first.client_telegram_id == 50
    assert local_payment.status is PaymentStatus.FAILED
    assert local_payment.manual_status is ManualPaymentStatus.REJECTED
    assert local_payment.rejection_reason == "Не найден перевод"
    assert appointment.status is AppointmentStatus.CANCELLED_BY_ADMIN
    assert reservation.status is ReservationStatus.CANCELLED
    assert window.status is AvailabilityWindowStatus.OPEN
    unit_of_work.appointments.add_history.assert_awaited_once()
    unit_of_work.audit.add.assert_awaited_once()
    unit_of_work.commit.assert_awaited_once()
    authorization.authorize.assert_awaited_with(
        business_id=1,
        telegram_id=4,
        permission=StaffPermission.REJECT_PREPAYMENTS,
    )


@pytest.mark.asyncio
async def test_receipt_reference_requires_fresh_view_permission() -> None:
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=actor())
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    local_payment = payment()
    local_payment.receipt_file_id = "telegram-file-id"
    local_payment.receipt_media_type = "document"
    unit_of_work.payments.get = AsyncMock(return_value=local_payment)
    unit_of_work.appointments.get = AsyncMock(return_value=SimpleNamespace(staff_member_id=2))
    service = PaymentAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
        MagicMock(),
    )

    result = await service.get_manual_receipt(actor(), 10)

    assert result is not None
    assert result.telegram_file_id == "telegram-file-id"
    authorization.authorize.assert_awaited_once_with(
        business_id=1,
        telegram_id=4,
        permission=StaffPermission.VIEW_PREPAYMENTS,
    )
