"""CRM cards, access control, tags, private notes and self-booking tests."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import ClientNote, ClientTag, User
from app.domain.enums import AppointmentStatus, PaymentMode, PaymentStatus, UserRole
from app.domain.errors import AuthorizationError, CrmStateError
from app.schemas.crm import ClientNoteCreate
from app.schemas.pagination import PageRequest
from app.schemas.service import AdminActor
from app.services.crm_service import CrmService

NOW = datetime(2026, 7, 22, 18, tzinfo=UTC)


def admin() -> AdminActor:
    return AdminActor(telegram_id=900, first_name="Master")


def client() -> User:
    return User(
        id=5,
        telegram_id=101,
        first_name="Анна",
        username="anna",
        phone="+79991234567",
        role=UserRole.CLIENT,
        marketing_consent_at=NOW,
        marketing_unsubscribed_at=None,
        is_blocked=False,
        is_self_booking_blocked=False,
    )


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=9))
    unit_of_work.users.get_by_id = AsyncMock(return_value=client())
    unit_of_work.crm.search_clients = AsyncMock(return_value=([client()], 1))
    unit_of_work.crm.list_client_tags = AsyncMock(return_value=[])
    unit_of_work.crm.list_notes = AsyncMock(return_value=[])
    unit_of_work.appointments.list_history_for_client = AsyncMock(return_value=([], 0))
    unit_of_work.appointments.count_statuses_for_client = AsyncMock(
        return_value={AppointmentStatus.COMPLETED: 3, AppointmentStatus.NO_SHOW: 1}
    )
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_admin_sees_card_aggregates_and_masked_list_phone() -> None:
    unit_of_work = build_uow()
    service = CrmService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    page = await service.list_clients(admin(), PageRequest(page=1, page_size=10))
    card = await service.get_card(admin(), 5)

    assert page.items[0].masked_phone == "***4567"
    assert page.items[0].marketing_subscribed
    assert card.phone == "+79991234567"
    assert card.completed_visits == 3
    assert card.no_shows == 1


@pytest.mark.asyncio
async def test_client_history_contains_local_time_and_latest_payment() -> None:
    unit_of_work = build_uow()
    target_appointment = SimpleNamespace(
        id=11,
        status=AppointmentStatus.COMPLETED,
        service_name_snapshot="Маникюр",
        master_name_snapshot="Руслана",
        price_snapshot=Decimal("2700.00"),
        prepayment_snapshot=Decimal("500.00"),
        currency_snapshot="RUB",
        payment_mode_snapshot=PaymentMode.MANUAL,
        completed_at=NOW,
        cancelled_at=None,
    )
    target_window = SimpleNamespace(
        start_at=datetime(2026, 7, 22, 10, tzinfo=UTC),
        end_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
    )
    payment = SimpleNamespace(
        id=31,
        status=PaymentStatus.PARTIALLY_REFUNDED,
        amount=Decimal("500.00"),
        refunded_amount=Decimal("100.00"),
        paid_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
    )
    unit_of_work.appointments.list_history_for_client.return_value = (
        [(target_appointment, target_window)],
        1,
    )
    unit_of_work.settings.get = AsyncMock(return_value=SimpleNamespace(timezone="Europe/Moscow"))
    unit_of_work.payments.get_latest_for_appointment = AsyncMock(return_value=payment)
    service = CrmService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    history = await service.list_history(
        admin(),
        5,
        PageRequest(page=1, page_size=5),
    )

    assert history.total == 1
    assert history.items[0].master_name == "Руслана"
    assert history.items[0].payment_status is PaymentStatus.PARTIALLY_REFUNDED
    assert history.items[0].refunded_amount == Decimal("100.00")
    assert history.items[0].timezone == "Europe/Moscow"
    unit_of_work.payments.get_latest_for_appointment.assert_awaited_once_with(11)


@pytest.mark.asyncio
async def test_non_admin_cannot_read_or_mutate_crm() -> None:
    factory = MagicMock()
    service = CrmService(factory, frozenset({900}))

    with pytest.raises(AuthorizationError):
        await service.get_card(AdminActor(telegram_id=901), 5)
    with pytest.raises(AuthorizationError):
        await service.add_note(AdminActor(telegram_id=901), 5, ClientNoteCreate(text="private"))

    factory.assert_not_called()


@pytest.mark.asyncio
async def test_active_tag_can_be_assigned_and_removed() -> None:
    unit_of_work = build_uow()
    unit_of_work.crm.get_tag = AsyncMock(return_value=ClientTag(id=7, name="VIP", is_active=True))
    unit_of_work.crm.assign_tag = AsyncMock(return_value=True)
    unit_of_work.crm.remove_tag = AsyncMock(return_value=True)
    service = CrmService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    assert await service.assign_tag(admin(), client_id=5, tag_id=7)
    assert await service.remove_tag(admin(), client_id=5, tag_id=7)
    assert unit_of_work.audit.add.await_count == 2


@pytest.mark.asyncio
async def test_archived_tag_cannot_be_newly_assigned() -> None:
    unit_of_work = build_uow()
    unit_of_work.crm.get_tag = AsyncMock(
        return_value=ClientTag(id=7, name="Старый", is_active=False)
    )
    unit_of_work.crm.assign_tag = AsyncMock()
    service = CrmService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(CrmStateError, match="Архивный"):
        await service.assign_tag(admin(), client_id=5, tag_id=7)

    unit_of_work.crm.assign_tag.assert_not_awaited()


@pytest.mark.asyncio
async def test_note_audit_contains_metadata_but_not_private_text() -> None:
    unit_of_work = build_uow()

    async def add_note(note: ClientNote) -> ClientNote:
        note.id = 12
        note.created_at = NOW
        return note

    unit_of_work.crm.add_note = AsyncMock(side_effect=add_note)
    service = CrmService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    note = await service.add_note(admin(), 5, ClientNoteCreate(text="Не звонить до 12:00"))

    assert note.text == "Не звонить до 12:00"
    changes = unit_of_work.audit.add.await_args.kwargs["changes"]
    assert changes == {"client_id": 5, "note_length": len("Не звонить до 12:00")}
    assert "Не звонить" not in str(changes)
