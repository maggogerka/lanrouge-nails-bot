"""Immutable client reviews and consent-aware administrator moderation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app.database.models import Review, ReviewRevision
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
from app.schemas.review import ReviewAdminUpdate, ReviewCreate, ReviewView
from app.schemas.service import AdminActor
from app.services.appointment_common import ensure_admin, ensure_owner_admin

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class ReviewService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def ensure_enabled(self) -> None:
        async with self._unit_of_work_factory() as uow:
            await self._ensure_enabled(uow)

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
                await self._ensure_enabled(uow)
                client = await uow.users.get_by_telegram_id(actor.telegram_id)
                if client is None or client.privacy_consent_at is None:
                    raise PrivacyConsentRequiredError(
                        "Сначала примите условия обработки данных через /start."
                    )
                appointment = await uow.appointments.get(appointment_id, for_update=True)
                if appointment is None:
                    raise EntityNotFoundError("Запись не найдена.")
                if appointment.client_id != client.id:
                    raise AuthorizationError("Нельзя оставить отзыв за другого клиента.")
                if appointment.status is not AppointmentStatus.COMPLETED:
                    raise ReviewStateError("Отзыв можно оставить только после завершённого визита.")
                if await uow.reviews.get_for_appointment(appointment.id) is not None:
                    raise ReviewStateError("Отзыв по этой записи уже оставлен.")
                review = await uow.reviews.add(
                    Review(
                        business_id=uow.business_id,
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
                return self._view(review, client.first_name or "Клиент")
        except IntegrityError as exc:
            raise ReviewStateError("Отзыв по этой записи уже оставлен.") from exc

    async def list_admin(
        self,
        actor: AdminActor,
        *,
        status: ReviewModerationStatus | None = None,
        deleted_only: bool = False,
        page: PageRequest | None = None,
    ) -> Page[ReviewView]:
        self._ensure_admin(actor)
        page = page or PageRequest()
        async with self._unit_of_work_factory() as uow:
            reviews, total = await uow.reviews.list_page(
                moderation_status=status,
                deleted_only=deleted_only,
                limit=page.page_size,
                offset=page.offset,
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
            settings = await uow.settings.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            if not settings.reviews_enabled:
                return Page(items=[], total=0, page=page.page, page_size=page.page_size)
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
        if status is ReviewModerationStatus.PENDING:
            raise ReviewStateError("Нельзя вернуть отзыв в статус ожидания.")
        return await self.edit_admin(
            actor,
            review_id,
            ReviewAdminUpdate(moderation_status=status),
            now=now,
            correlation_id=correlation_id,
        )

    async def get_admin(self, actor: AdminActor, review_id: int) -> ReviewView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as uow:
            review = await uow.reviews.get(review_id)
            if review is None:
                raise EntityNotFoundError("Отзыв не найден.")
            return await self._with_client(uow, review)

    async def edit_admin(
        self,
        actor: AdminActor,
        review_id: int,
        values: ReviewAdminUpdate,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ReviewView:
        self._ensure_admin(actor)
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as uow:
            admin = await uow.users.get_or_create_admin(actor)
            review = await uow.reviews.get(review_id, for_update=True)
            if review is None:
                raise EntityNotFoundError("Отзыв не найден.")
            if review.deleted_at is not None:
                raise ReviewStateError("Удалённый отзыв сначала нужно восстановить.")
            changes = values.model_dump(exclude_unset=True)
            new_status = changes.get("moderation_status", review.moderation_status)
            if new_status is ReviewModerationStatus.APPROVED and not review.publication_consent:
                raise ReviewStateError("Клиент не разрешила публикацию этого отзыва.")
            await uow.reviews.add_revision(
                ReviewRevision(
                    review_id=review.id,
                    rating=review.rating,
                    text=review.text,
                    moderation_status=review.moderation_status,
                    published_at=review.published_at,
                    changed_by_admin_id=admin.id,
                )
            )
            before = {
                "rating": review.rating,
                "has_text": review.text is not None,
                "status": review.moderation_status.value,
            }
            for field, value in changes.items():
                setattr(review, field, value)
            review.published_at = (
                current if review.moderation_status is ReviewModerationStatus.APPROVED else None
            )
            review.edited_at = current
            review.edited_by_admin_id = admin.id
            review.is_admin_edited = True
            await uow.audit.add(
                actor_user_id=admin.id,
                action="review.edited_by_admin",
                entity_type="review",
                entity_id=str(review.id),
                changes={
                    "before": before,
                    "after": {
                        "rating": review.rating,
                        "has_text": review.text is not None,
                        "status": review.moderation_status.value,
                    },
                },
                correlation_id=correlation_id,
            )
            client = await uow.users.get_by_id(review.client_id)
            await uow.commit()
            return self._view(
                review, client.first_name if client and client.first_name else "Клиент"
            )

    async def soft_delete(
        self,
        actor: AdminActor,
        review_id: int,
        reason: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ReviewView:
        self._ensure_admin(actor)
        normalized_reason = reason.strip()
        if not 1 <= len(normalized_reason) <= 500:
            raise ReviewStateError("Укажите причину удаления длиной до 500 символов.")
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as uow:
            admin = await uow.users.get_or_create_admin(actor)
            review = await uow.reviews.get(review_id, for_update=True)
            if review is None:
                raise EntityNotFoundError("Отзыв не найден.")
            if review.deleted_at is not None:
                raise ReviewStateError("Отзыв уже удалён.")
            review.deleted_at = current
            review.deleted_by_user_id = admin.id
            review.deletion_reason = normalized_reason
            review.published_at = None
            await self._audit_lifecycle(
                uow,
                admin.id,
                review.id,
                "review.deleted",
                correlation_id,
            )
            client = await uow.users.get_by_id(review.client_id)
            await uow.commit()
            return self._view(
                review, client.first_name if client and client.first_name else "Клиент"
            )

    async def restore(
        self,
        actor: AdminActor,
        review_id: int,
        *,
        correlation_id: str | None = None,
    ) -> ReviewView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as uow:
            admin = await uow.users.get_or_create_admin(actor)
            review = await uow.reviews.get(review_id, for_update=True)
            if review is None:
                raise EntityNotFoundError("Отзыв не найден.")
            if review.deleted_at is None:
                raise ReviewStateError("Отзыв не удалён.")
            review.deleted_at = None
            review.deleted_by_user_id = None
            review.deletion_reason = None
            review.moderation_status = ReviewModerationStatus.PENDING
            review.published_at = None
            await self._audit_lifecycle(
                uow,
                admin.id,
                review.id,
                "review.restored",
                correlation_id,
            )
            client = await uow.users.get_by_id(review.client_id)
            await uow.commit()
            return self._view(
                review, client.first_name if client and client.first_name else "Клиент"
            )

    async def hard_delete(
        self,
        actor: AdminActor,
        review_id: int,
        *,
        correlation_id: str | None = None,
    ) -> None:
        ensure_owner_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as uow:
            ensure_owner_admin(
                actor,
                self._admin_telegram_ids,
                business_id=uow.business_id,
            )
            admin = await uow.users.get_or_create_admin(actor)
            review = await uow.reviews.get(review_id, for_update=True)
            if review is None:
                raise EntityNotFoundError("Отзыв не найден.")
            if review.deleted_at is None:
                raise ReviewStateError("Сначала выполните обычное удаление отзыва.")
            await uow.reviews.hard_delete(review)
            await uow.audit.add(
                actor_user_id=admin.id,
                action="review.permanently_deleted",
                entity_type="review",
                entity_id=str(review_id),
                changes={"hard_deleted": True},
                correlation_id=correlation_id,
            )
            await uow.commit()

    async def _with_client(self, uow: SqlAlchemyUnitOfWork, review: Review) -> ReviewView:
        client = await uow.users.get_by_id(review.client_id)
        return self._view(review, client.first_name if client and client.first_name else "Клиент")

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
            edited_at=review.edited_at,
            is_admin_edited=bool(review.is_admin_edited),
            deleted_at=review.deleted_at,
            deletion_reason=review.deletion_reason,
        )

    def _ensure_admin(self, actor: AdminActor) -> None:
        ensure_admin(actor, self._admin_telegram_ids)

    @staticmethod
    async def _ensure_enabled(uow: SqlAlchemyUnitOfWork) -> None:
        settings = await uow.settings.get()
        if settings is None:
            raise RuntimeError("Business settings row is missing")
        if not settings.reviews_enabled:
            raise ReviewStateError("Отзывы временно отключены.")

    @staticmethod
    async def _audit_lifecycle(
        uow: SqlAlchemyUnitOfWork,
        actor_user_id: int,
        review_id: int,
        action: str,
        correlation_id: str | None,
    ) -> None:
        await uow.audit.add(
            actor_user_id=actor_user_id,
            action=action,
            entity_type="review",
            entity_id=str(review_id),
            changes={"lifecycle_changed": True},
            correlation_id=correlation_id,
        )

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
