"""Review ownership, completed-visit and publication-consent tests."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.database.models import Appointment, Review, ReviewRevision, User
from app.domain.enums import AppointmentStatus, ReviewModerationStatus
from app.domain.errors import AuthorizationError, ReviewStateError
from app.schemas.booking import ClientActor
from app.schemas.review import ReviewAdminUpdate, ReviewCreate
from app.schemas.service import AdminActor
from app.services.review_service import ReviewService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def appointment(status: AppointmentStatus = AppointmentStatus.COMPLETED) -> Appointment:
    return Appointment(
        id=11,
        client_id=5,
        window_id=7,
        service_id=3,
        service_name_snapshot="Консультация",
        price_snapshot=Decimal("2500"),
        duration_min_snapshot=120,
        duration_max_snapshot=180,
        status=status,
    )


def build_uow(
    *,
    status: AppointmentStatus = AppointmentStatus.COMPLETED,
    owner_id: int = 5,
    existing: Review | None = None,
) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    client = User(
        id=5,
        telegram_id=101,
        first_name="Анна",
        privacy_consent_at=NOW,
    )
    target = appointment(status)
    target.client_id = owner_id
    unit_of_work.users.get_by_telegram_id = AsyncMock(return_value=client)
    unit_of_work.users.get_by_id = AsyncMock(return_value=client)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=User(id=9, telegram_id=900))
    unit_of_work.appointments.get = AsyncMock(return_value=target)
    unit_of_work.settings.get = AsyncMock(return_value=SimpleNamespace(reviews_enabled=True))
    unit_of_work.reviews.get_for_appointment = AsyncMock(return_value=existing)

    async def save(review: Review) -> Review:
        review.id = 21
        review.created_at = NOW
        review.updated_at = NOW
        return review

    unit_of_work.reviews.add = AsyncMock(side_effect=save)
    unit_of_work.reviews.add_revision = AsyncMock()
    unit_of_work.reviews.hard_delete = AsyncMock()
    unit_of_work.reviews.list_page = AsyncMock(return_value=([], 0))
    unit_of_work.reviews.list_published = AsyncMock(return_value=([], 0))
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


def test_rating_is_limited_to_one_through_five() -> None:
    with pytest.raises(ValidationError):
        ReviewCreate(rating=0)
    with pytest.raises(ValidationError):
        ReviewCreate(rating=6)


@pytest.mark.asyncio
async def test_only_owner_can_review_completed_appointment() -> None:
    not_completed = build_uow(status=AppointmentStatus.CONFIRMED)
    service = ReviewService(lambda: not_completed, frozenset({900}))  # type: ignore[arg-type]
    with pytest.raises(ReviewStateError):
        await service.submit(ClientActor(telegram_id=101), 11, ReviewCreate(rating=5))

    other_owner = build_uow(owner_id=99)
    service = ReviewService(lambda: other_owner, frozenset({900}))  # type: ignore[arg-type]
    with pytest.raises(AuthorizationError):
        await service.submit(ClientActor(telegram_id=101), 11, ReviewCreate(rating=5))


@pytest.mark.asyncio
async def test_one_review_per_appointment_and_client_text_is_not_audited() -> None:
    unit_of_work = build_uow()
    service = ReviewService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    review = await service.submit(
        ClientActor(telegram_id=101),
        11,
        ReviewCreate(rating=5, text="Очень понравилось", publication_consent=True),
    )

    assert review.text == "Очень понравилось"
    changes = unit_of_work.audit.add.await_args.kwargs["changes"]
    assert "Очень понравилось" not in str(changes)

    unit_of_work.reviews.get_for_appointment.return_value = Review(id=22)
    with pytest.raises(ReviewStateError):
        await service.submit(ClientActor(telegram_id=101), 11, ReviewCreate(rating=4))


@pytest.mark.asyncio
async def test_approval_requires_explicit_publication_consent() -> None:
    unit_of_work = build_uow()
    target = Review(
        id=21,
        appointment_id=11,
        client_id=5,
        rating=5,
        publication_consent=False,
        moderation_status=ReviewModerationStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )
    unit_of_work.reviews.get = AsyncMock(return_value=target)
    service = ReviewService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(ReviewStateError):
        await service.moderate(
            AdminActor(telegram_id=900),
            target.id,
            ReviewModerationStatus.APPROVED,
            now=NOW,
        )

    target.publication_consent = True
    approved = await service.moderate(
        AdminActor(telegram_id=900),
        target.id,
        ReviewModerationStatus.APPROVED,
        now=NOW,
    )
    assert approved.moderation_status is ReviewModerationStatus.APPROVED
    assert approved.published_at == NOW


@pytest.mark.asyncio
async def test_disabled_reviews_reject_submission_and_hide_public_list() -> None:
    unit_of_work = build_uow()
    unit_of_work.settings.get.return_value.reviews_enabled = False
    service = ReviewService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(ReviewStateError, match="отключены"):
        await service.submit(ClientActor(telegram_id=101), 11, ReviewCreate(rating=5))
    page = await service.list_public()

    assert page.total == 0
    unit_of_work.reviews.list_published.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_edit_creates_revision_without_auditing_full_text() -> None:
    unit_of_work = build_uow()
    target = Review(
        id=21,
        appointment_id=11,
        client_id=5,
        rating=4,
        text="Исходный текст",
        publication_consent=True,
        moderation_status=ReviewModerationStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )
    unit_of_work.reviews.get = AsyncMock(return_value=target)
    service = ReviewService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    edited = await service.edit_admin(
        AdminActor(telegram_id=900),
        21,
        ReviewAdminUpdate(
            rating=5,
            text="Новый административный текст",
            moderation_status=ReviewModerationStatus.APPROVED,
        ),
        now=NOW,
        correlation_id="edit-review",
    )

    revision = unit_of_work.reviews.add_revision.await_args.args[0]
    assert isinstance(revision, ReviewRevision)
    assert revision.rating == 4
    assert revision.text == "Исходный текст"
    assert edited.rating == 5
    assert edited.is_admin_edited
    audit = unit_of_work.audit.add.await_args.kwargs
    assert "Новый административный текст" not in str(audit["changes"])
    assert audit["action"] == "review.edited_by_admin"


@pytest.mark.asyncio
async def test_soft_delete_restore_and_hard_delete_have_separate_lifecycle() -> None:
    unit_of_work = build_uow()
    target = Review(
        id=21,
        appointment_id=11,
        client_id=5,
        rating=5,
        publication_consent=True,
        moderation_status=ReviewModerationStatus.APPROVED,
        published_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    unit_of_work.reviews.get = AsyncMock(return_value=target)
    service = ReviewService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    deleted = await service.soft_delete(
        AdminActor(telegram_id=900), 21, "Нарушение правил", now=NOW
    )
    assert deleted.deleted_at == NOW
    assert deleted.published_at is None

    restored = await service.restore(AdminActor(telegram_id=900), 21)
    assert restored.deleted_at is None
    assert restored.moderation_status is ReviewModerationStatus.PENDING

    target.deleted_at = NOW
    await service.hard_delete(AdminActor(telegram_id=900), 21)
    unit_of_work.reviews.hard_delete.assert_awaited_once_with(target)
