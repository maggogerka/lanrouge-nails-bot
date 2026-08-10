"""Live authorization and safe projections for the Telegram payment panel."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models.payment import Payment
from app.domain.enums import PaymentMode, PaymentStatus, StaffRole
from app.domain.payments import PaymentType
from app.keyboards.admin.payments import payments_keyboard
from app.schemas.authorization import StaffContext, StaffPermission
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
    )


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
        permission=StaffPermission.VIEW_PAYMENTS,
    )
    assert result[0].id == 10
    assert "idempotency_key" not in result[0].model_dump()
    assert "safe_metadata" not in result[0].model_dump()


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
