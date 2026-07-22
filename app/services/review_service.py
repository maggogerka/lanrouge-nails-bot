"""Immutable client reviews and consent-aware administrator moderation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app.database.models import Review
from app.domain.enums import AppointmentStatus, ReviewModerationStatus
from app.domain.errors import (
    AuthorizationError,
    EntityNotFoundError,
    PrivacyConsentRequiredError,
    ReviewStateError,
)
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor
from app.schemas.pagination import Page, PageRequest
from app.schemas.review import ReviewCreate, ReviewView
from app.schemas.service import AdminActor

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class ReviewService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def submit(
        self,
        actor: ClientActor,
        appointment_id: int,
        values: ReviewCreate,
        *,
        correlation_id: str | None = None,
    ) -> ReviewView:
        try:
            async with self._unit_of_work_factory() as uow:
                client = await uow.users.get_by_telegram_id(actor.telegram_id)
                if client is None or client.privacy_consent_at is None:
                    raise PrivacyConsentRequiredError(
                        "Сначала примите условия обработки данных через /start."
                    )
                appointment = await uow.appointments.get(appointment_id, for_update=True)
                if appointment is None:
                    raise EntityNotFoundError("Запись не найдена.")
                if appointment.client_id != client.id:
                    raise AuthorizationError("Нельзя оставить отзыв за другую клиентку.")
                if appointment.status is not AppointmentStatus.COMPLETED:
                    raise ReviewStateError("Отзыв можно оставить только после завершённого визита.")
                if await uow.reviews.get_for_appointment(appointment.id) is not None:
                    raise ReviewStateError("Отзыв по этой записи уже оставлен.")
                review = await uow.reviews.add(
                    Review(
                        appointment_id=appointment.id,
                        client_id=client.id,
                        rating=values.rating,
                        text=values.text,
                        publication_consent=values.publication_consent,
                        moderation_status=ReviewModerationStatus.PENDING,
                    )
                )
                await uow.audit.add(
                    actor_user_id=client.id,
                    action="review.submitted",
                    entity_type="review",
                    entity_id=str(review.id),
                    changes={
                        "appointment_id": appointment.id,
                        "rating": values.rating,
                        "has_text": values.text is not None,
                        "publication_consent": values.publication_consent,
                    },
                    correlation_id=correlation_id,
                )
                await uow.commit()
                return self._view(review, client.first_name or "Клиентка")
        except IntegrityError as exc:
            raise ReviewStateError("Отзыв по этой записи уже оставлен.") from exc

    async def list_admin(
        self,
        actor: AdminActor,
        *,
        status: ReviewModerationStatus | None = None,
        page: PageRequest | None = None,
    ) -> Page[ReviewView]:
        self._ensure_admin(actor)
        page = page or PageRequest()
        async with self._unit_of_work_factory() as uow:
            reviews, total = await uow.reviews.list_page(
                moderation_status=status, limit=page.page_size, offset=page.offset
            )
            return Page(
                items=[await self._with_client(uow, review) for review in reviews],
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def list_public(self, page: PageRequest | None = None) -> Page[ReviewView]:
        page = page or PageRequest()
        async with self._unit_of_work_factory() as uow:
            reviews, total = await uow.reviews.list_published(
                limit=page.page_size, offset=page.offset
            )
            return Page(
                items=[await self._with_client(uow, review) for review in reviews],
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def moderate(
        self,
        actor: AdminActor,
        review_id: int,
        status: ReviewModerationStatus,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ReviewView:
        self._ensure_admin(actor)
        if status is ReviewModerationStatus.PENDING:
            raise ReviewStateError("Нельзя вернуть отзыв в статус ожидания.")
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        async with self._unit_of_work_factory() as uow:
            admin = await uow.users.get_or_create_admin(actor)
            review = await uow.reviews.get(review_id, for_update=True)
            if review is None:
                raise EntityNotFoundError("Отзыв не найден.")
            if status is ReviewModerationStatus.APPROVED and not review.publication_consent:
                raise ReviewStateError("Клиентка не разрешила публикацию этого отзыва.")
            previous = review.moderation_status
            review.moderation_status = status
            review.published_at = (
                current.astimezone(UTC) if status is ReviewModerationStatus.APPROVED else None
            )
            await uow.audit.add(
                actor_user_id=admin.id,
                action="review.moderated",
                entity_type="review",
                entity_id=str(review.id),
                changes={"status": {"before": previous.value, "after": status.value}},
                correlation_id=correlation_id,
            )
            client = await uow.users.get_by_id(review.client_id)
            await uow.commit()
            return self._view(
                review, client.first_name if client and client.first_name else "Клиентка"
            )

    async def _with_client(self, uow: SqlAlchemyUnitOfWork, review: Review) -> ReviewView:
        client = await uow.users.get_by_id(review.client_id)
        return self._view(review, client.first_name if client and client.first_name else "Клиентка")

    @staticmethod
    def _view(review: Review, client_name: str) -> ReviewView:
        return ReviewView(
            id=review.id,
            appointment_id=review.appointment_id,
            client_id=review.client_id,
            client_name=client_name,
            rating=review.rating,
            text=review.text,
            publication_consent=review.publication_consent,
            moderation_status=review.moderation_status,
            published_at=review.published_at,
            created_at=review.created_at,
        )

    def _ensure_admin(self, actor: AdminActor) -> None:
        if actor.telegram_id not in self._admin_telegram_ids:
            raise AuthorizationError("Недостаточно прав администратора.")
