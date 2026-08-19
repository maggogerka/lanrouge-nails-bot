"""Client payment projections must remain paginated and owner-scoped."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import ManualPaymentStatus, PaymentMode, PaymentStatus
from app.domain.errors import EntityNotFoundError
from app.domain.payments import PaymentType
from app.keyboards.client.payments import client_payment_details_keyboard
from app.schemas.booking import ClientActor
from app.schemas.pagination import PageRequest
from app.schemas.payment import ClientPaymentSection
from app.services.client_payment_service import ClientPaymentService

NOW = datetime(2026, 8, 19, 10, tzinfo=UTC)


def actor() -> ClientActor:
    return ClientActor(telegram_id=777, username="client", first_name="Клиент")


def payment() -> SimpleNamespace:
    return SimpleNamespace(
        id=51,
        business_id=1,
        appointment_id=41,
        provider=PaymentMode.MANUAL,
        provider_payment_id=None,
        amount=Decimal("500.00"),
        refunded_amount=Decimal("0.00"),
        currency="RUB",
        status=PaymentStatus.PENDING,
        payment_type=PaymentType.DEPOSIT,
        confirmation_url=None,
        expires_at=NOW + timedelta(minutes=15),
        paid_at=None,
        cancelled_at=None,
        refunded_at=None,
        manual_status=ManualPaymentStatus.AWAITING_PAYMENT,
        client_reported_at=None,
        review_started_at=None,
        reviewed_at=None,
        rejection_reason=None,
        has_receipt=False,
        created_at=NOW,
    )


def appointment() -> SimpleNamespace:
    return SimpleNamespace(
        scheduled_start_at=NOW + timedelta(days=2),
        scheduled_end_at=NOW + timedelta(days=2, hours=1),
        service_name_snapshot="Маникюр",
        master_name_snapshot="Руслана",
    )


def uow(*, owned_row: tuple[SimpleNamespace, SimpleNamespace] | None) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_by_telegram_id = AsyncMock(return_value=SimpleNamespace(id=17))
    unit_of_work.settings.get = AsyncMock(return_value=SimpleNamespace(timezone="Europe/Moscow"))
    unit_of_work.reservations.payment_settings = AsyncMock(
        return_value=SimpleNamespace(manual_payment_instructions="Перевод по СБП: +7 900 000-00-00")
    )
    unit_of_work.payments.get_for_client = AsyncMock(return_value=owned_row)
    unit_of_work.payments.count_for_client = AsyncMock(return_value=1)
    unit_of_work.payments.list_for_client = AsyncMock(
        return_value=[] if owned_row is None else [owned_row]
    )
    return unit_of_work


@pytest.mark.asyncio
async def test_client_payment_page_uses_resolved_client_id_and_keeps_instructions() -> None:
    unit_of_work = uow(owned_row=(payment(), appointment()))
    service = ClientPaymentService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.list_my_page(
        actor(), ClientPaymentSection.ACTIVE, PageRequest(page_size=7)
    )

    assert result.total == 1
    assert result.items[0].manual_payment_instructions == "Перевод по СБП: +7 900 000-00-00"
    unit_of_work.payments.list_for_client.assert_awaited_once()
    assert unit_of_work.payments.list_for_client.await_args.args[0] == 17


@pytest.mark.asyncio
async def test_client_payment_counts_share_the_same_owner_scope() -> None:
    unit_of_work = uow(owned_row=(payment(), appointment()))
    unit_of_work.payments.count_for_client = AsyncMock(side_effect=[2, 5])
    service = ClientPaymentService(lambda: unit_of_work)  # type: ignore[arg-type]

    assert await service.get_my_counts(actor()) == (2, 5)
    assert [call.args[0] for call in unit_of_work.payments.count_for_client.await_args_list] == [
        17,
        17,
    ]


@pytest.mark.asyncio
async def test_client_cannot_open_payment_not_returned_by_owner_scoped_query() -> None:
    unit_of_work = uow(owned_row=None)
    service = ClientPaymentService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(EntityNotFoundError, match="Оплата не найдена"):
        await service.get_my_details(actor(), 999)

    unit_of_work.payments.get_for_client.assert_awaited_once_with(999, 17)


@pytest.mark.asyncio
async def test_pending_manual_payment_exposes_only_expected_client_actions() -> None:
    unit_of_work = uow(owned_row=(payment(), appointment()))
    service = ClientPaymentService(lambda: unit_of_work)  # type: ignore[arg-type]
    view = await service.get_my_details(actor(), 51)

    keyboard = client_payment_details_keyboard(view, section=ClientPaymentSection.ACTIVE, page=1)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert "✅ Я оплатил" in labels
    assert "⬅️ К списку" in labels
    assert all("подтвердить" not in label.casefold() for label in labels)
