"""Administrator-only client cards, search, tags, notes and booking controls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from app.database.models import ClientNote, ClientTag, User
from app.domain.enums import AppointmentStatus
from app.domain.errors import CrmStateError, EntityNotFoundError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.crm import (
    ClientAppointmentHistoryView,
    ClientAppointmentView,
    ClientCardView,
    ClientNoteCreate,
    ClientNoteView,
    ClientPage,
    ClientSummaryView,
    ClientTagCreate,
    ClientTagView,
    safe_telegram_profile_url,
)
from app.schemas.pagination import Page, PageRequest
from app.schemas.service import AdminActor
from app.services.appointment_common import ensure_admin

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class CrmService:
    """Protect private CRM data at the application-service boundary."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def list_clients(
        self,
        actor: AdminActor,
        page: PageRequest,
        *,
        query: str | None = None,
        tag_id: int | None = None,
    ) -> ClientPage:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            users, total = await unit_of_work.crm.search_clients(
                query=query,
                tag_id=tag_id,
                limit=page.page_size,
                offset=page.offset,
            )
            return ClientPage(
                items=[self._summary(user) for user in users],
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def get_card(self, actor: AdminActor, client_id: int) -> ClientCardView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_by_id(client_id)
            if user is None:
                raise EntityNotFoundError("Клиент больше не существует.")
            rows, total = await unit_of_work.appointments.list_history_for_client(
                client_id, limit=10, offset=0
            )
            counts = await unit_of_work.appointments.count_statuses_for_client(client_id)
            tags = await unit_of_work.crm.list_client_tags(client_id)
            notes = await unit_of_work.crm.list_notes(client_id)
            summary = self._summary(user)
            return ClientCardView(
                **summary.model_dump(),
                phone=user.phone,
                completed_visits=counts.get(AppointmentStatus.COMPLETED, 0),
                cancellations=(
                    counts.get(AppointmentStatus.CANCELLED_BY_CLIENT, 0)
                    + counts.get(AppointmentStatus.CANCELLED_BY_ADMIN, 0)
                ),
                no_shows=counts.get(AppointmentStatus.NO_SHOW, 0),
                appointments_total=total,
                appointments=[
                    ClientAppointmentView(
                        id=appointment.id,
                        status=appointment.status,
                        service_name=appointment.service_name_snapshot,
                        price=appointment.price_snapshot,
                        start_at=window.start_at,
                        end_at=window.end_at,
                    )
                    for appointment, window in rows
                ],
                tags=[ClientTagView.model_validate(tag) for tag in tags],
                notes=[ClientNoteView.model_validate(note) for note in notes],
            )

    async def list_history(
        self,
        actor: AdminActor,
        client_id: int,
        page: PageRequest,
    ) -> Page[ClientAppointmentHistoryView]:
        """Return a bounded local-time history with the latest payment snapshot."""

        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            client = await unit_of_work.users.get_by_id(client_id)
            settings = await unit_of_work.settings.get()
            if client is None:
                raise EntityNotFoundError("Клиент больше не существует.")
            if settings is None:
                raise EntityNotFoundError("Настройки бизнеса не найдены.")
            rows, total = await unit_of_work.appointments.list_history_for_client(
                client_id,
                limit=page.page_size,
                offset=page.offset,
            )
            items: list[ClientAppointmentHistoryView] = []
            for appointment, window in rows:
                payment = await unit_of_work.payments.get_latest_for_appointment(appointment.id)
                items.append(
                    ClientAppointmentHistoryView(
                        id=appointment.id,
                        status=appointment.status,
                        service_name=appointment.service_name_snapshot,
                        master_name=appointment.master_name_snapshot,
                        price=appointment.price_snapshot,
                        prepayment_amount=appointment.prepayment_snapshot,
                        currency=appointment.currency_snapshot,
                        payment_mode=appointment.payment_mode_snapshot,
                        start_at=window.start_at,
                        end_at=window.end_at,
                        timezone=settings.timezone,
                        completed_at=appointment.completed_at,
                        cancelled_at=appointment.cancelled_at,
                        payment_id=payment.id if payment is not None else None,
                        payment_status=payment.status if payment is not None else None,
                        payment_amount=payment.amount if payment is not None else Decimal("0"),
                        refunded_amount=(
                            payment.refunded_amount if payment is not None else Decimal("0")
                        ),
                        paid_at=payment.paid_at if payment is not None else None,
                    )
                )
            return Page(
                items=items,
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def list_tags(
        self, actor: AdminActor, *, active_only: bool = False
    ) -> list[ClientTagView]:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            tags = await unit_of_work.crm.list_tags(active_only=active_only)
            return [ClientTagView.model_validate(tag) for tag in tags]

    async def create_tag(
        self,
        actor: AdminActor,
        values: ClientTagCreate,
        *,
        correlation_id: str | None = None,
    ) -> ClientTagView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            tag = await unit_of_work.crm.add_tag(
                ClientTag(
                    business_id=unit_of_work.business_id,
                    name=values.name,
                    marker=values.marker,
                    is_active=True,
                )
            )
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="client_tag.created",
                entity_type="client_tag",
                entity_id=str(tag.id),
                changes={"is_active": True},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return ClientTagView.model_validate(tag)

    async def assign_tag(
        self,
        actor: AdminActor,
        *,
        client_id: int,
        tag_id: int,
        correlation_id: str | None = None,
    ) -> bool:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            client = await unit_of_work.users.get_by_id(client_id)
            tag = await unit_of_work.crm.get_tag(tag_id)
            if client is None or tag is None:
                raise EntityNotFoundError("Клиент или тег больше не существует.")
            if not tag.is_active:
                raise CrmStateError("Архивный тег нельзя назначить клиенту.")
            created = await unit_of_work.crm.assign_tag(
                user_id=client_id, tag_id=tag_id, assigned_by=actor_user.id
            )
            if created:
                await unit_of_work.audit.add(
                    actor_user_id=actor_user.id,
                    action="client_tag.assigned",
                    entity_type="user",
                    entity_id=str(client_id),
                    changes={"tag_id": tag_id},
                    correlation_id=correlation_id,
                )
                await unit_of_work.commit()
            return created

    async def remove_tag(
        self,
        actor: AdminActor,
        *,
        client_id: int,
        tag_id: int,
        correlation_id: str | None = None,
    ) -> bool:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            removed = await unit_of_work.crm.remove_tag(user_id=client_id, tag_id=tag_id)
            if removed:
                await unit_of_work.audit.add(
                    actor_user_id=actor_user.id,
                    action="client_tag.removed",
                    entity_type="user",
                    entity_id=str(client_id),
                    changes={"tag_id": tag_id},
                    correlation_id=correlation_id,
                )
                await unit_of_work.commit()
            return removed

    async def set_tag_active(
        self,
        actor: AdminActor,
        tag_id: int,
        *,
        is_active: bool,
        correlation_id: str | None = None,
    ) -> ClientTagView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            tag = await unit_of_work.crm.get_tag(tag_id, for_update=True)
            if tag is None:
                raise EntityNotFoundError("Тег больше не существует.")
            previous = tag.is_active
            tag.is_active = is_active
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="client_tag.activity_changed",
                entity_type="client_tag",
                entity_id=str(tag.id),
                changes={"is_active": {"before": previous, "after": is_active}},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return ClientTagView.model_validate(tag)

    async def add_note(
        self,
        actor: AdminActor,
        client_id: int,
        values: ClientNoteCreate,
        *,
        correlation_id: str | None = None,
    ) -> ClientNoteView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            client = await unit_of_work.users.get_by_id(client_id)
            if client is None:
                raise EntityNotFoundError("Клиент больше не существует.")
            note = await unit_of_work.crm.add_note(
                ClientNote(
                    business_id=unit_of_work.business_id,
                    client_id=client_id,
                    author_id=actor_user.id,
                    text=values.text,
                )
            )
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="client_note.created",
                entity_type="client_note",
                entity_id=str(note.id),
                changes={"client_id": client_id, "note_length": len(values.text)},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return ClientNoteView.model_validate(note)

    async def archive_note(
        self,
        actor: AdminActor,
        note_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> None:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            note = await unit_of_work.crm.get_note(note_id, for_update=True)
            if note is None:
                raise EntityNotFoundError("Заметка больше не существует.")
            note.archived_at = self._aware_now(now)
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="client_note.archived",
                entity_type="client_note",
                entity_id=str(note.id),
                changes={"client_id": note.client_id},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()

    async def set_self_booking_blocked(
        self,
        actor: AdminActor,
        client_id: int,
        *,
        blocked: bool,
        reason: str | None = None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ClientCardView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            client = await unit_of_work.users.get_by_id(client_id, for_update=True)
            if client is None:
                raise EntityNotFoundError("Клиент больше не существует.")
            client.is_self_booking_blocked = blocked
            client.self_booking_blocked_at = self._aware_now(now) if blocked else None
            client.self_booking_blocked_by = actor_user.id if blocked else None
            client.self_booking_block_reason = reason.strip()[:500] if blocked and reason else None
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="client.self_booking_changed",
                entity_type="user",
                entity_id=str(client.id),
                changes={"is_self_booking_blocked": blocked},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
        return await self.get_card(actor, client.id)

    @staticmethod
    def _summary(user: User) -> ClientSummaryView:
        display_name = " ".join(
            value for value in (user.first_name, user.last_name) if value
        ).strip()
        return ClientSummaryView(
            id=user.id,
            telegram_id=user.telegram_id,
            display_name=display_name or user.username or f"Клиент #{user.id}",
            username=user.username,
            telegram_profile_url=safe_telegram_profile_url(user.username),
            masked_phone=_mask_phone(user.phone),
            marketing_subscribed=(
                user.marketing_consent_at is not None and user.marketing_unsubscribed_at is None
            ),
            is_blocked=bool(user.is_blocked),
            is_self_booking_blocked=bool(user.is_self_booking_blocked),
        )

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)


def _mask_phone(phone: str | None) -> str | None:
    if phone is None or len(phone) < 4:
        return phone
    return f"***{phone[-4:]}"
