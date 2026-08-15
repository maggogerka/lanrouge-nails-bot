"""Client and administrator waitlist use cases."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.database.models import BusinessSettings, User, WaitlistEntry
from app.domain.enums import AvailabilityWindowStatus, WaitlistStatus
from app.domain.errors import (
    AuthorizationError,
    EntityNotFoundError,
    PrivacyConsentRequiredError,
    WaitlistStateError,
)
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor
from app.schemas.pagination import Page, PageRequest
from app.schemas.service import AdminActor
from app.schemas.waitlist import AdminWaitlistView, WaitlistCreate, WaitlistView
from app.services.appointment_common import ensure_admin
from app.services.waitlist_matching import enqueue_waitlist_matches

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class WaitlistService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def create(
        self,
        actor: ClientActor,
        values: WaitlistCreate,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> WaitlistView:
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as uow:
            client = await self._client(uow, actor.telegram_id)
            settings = await self._settings(uow)
            if not settings.waitlist_enabled:
                raise WaitlistStateError("Лист ожидания сейчас отключён.")
            service = await uow.services.get(values.service_id)
            if service is None or not service.is_active:
                raise EntityNotFoundError("Услуга больше недоступна.")
            today = current.astimezone(ZoneInfo(settings.timezone)).date()
            if values.date_from < today or values.date_to < today:
                raise WaitlistStateError("Для листа ожидания выберите будущие даты.")
            expires_local = datetime.combine(
                values.date_to + timedelta(days=1), time.min, ZoneInfo(settings.timezone)
            )
            configured_expiry = current + timedelta(days=settings.waitlist_default_expiration_days)
            entry = await uow.waitlist.add(
                WaitlistEntry(
                    business_id=uow.business_id,
                    client_id=client.id,
                    service_id=service.id,
                    date_from=values.date_from,
                    date_to=values.date_to,
                    preferred_dates=values.preferred_dates,
                    preferred_time_from=values.preferred_time_from,
                    preferred_time_to=values.preferred_time_to,
                    status=WaitlistStatus.ACTIVE,
                    expires_at=min(expires_local.astimezone(UTC), configured_expiry),
                )
            )
            await uow.audit.add(
                actor_user_id=client.id,
                action="waitlist.created",
                entity_type="waitlist_entry",
                entity_id=str(entry.id),
                changes={"service_id": service.id},
                correlation_id=correlation_id,
            )
            await uow.commit()
            return self._view(entry, service.name)

    async def list_my(
        self,
        actor: ClientActor,
        page: PageRequest | None = None,
    ) -> Page[WaitlistView]:
        page = page or PageRequest()
        async with self._unit_of_work_factory() as uow:
            client = await self._client(uow, actor.telegram_id)
            entries, total = await uow.waitlist.list_for_client(
                client.id, active_only=False, limit=page.page_size, offset=page.offset
            )
            return Page(
                items=[
                    self._view(entry, await self._service_name(uow, entry.service_id))
                    for entry in entries
                ],
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def cancel_my(
        self,
        actor: ClientActor,
        entry_id: int,
        *,
        correlation_id: str | None = None,
    ) -> WaitlistView:
        async with self._unit_of_work_factory() as uow:
            client = await self._client(uow, actor.telegram_id)
            entry = await self._entry(uow, entry_id, for_update=True)
            if entry.client_id != client.id:
                raise AuthorizationError("Заявка не принадлежит вам.")
            await self._archive(uow, entry, client.id, correlation_id)
            service_name = await self._service_name(uow, entry.service_id)
            await uow.commit()
            return self._view(entry, service_name)

    async def get_my(self, actor: ClientActor, entry_id: int) -> WaitlistView:
        async with self._unit_of_work_factory() as uow:
            client = await self._client(uow, actor.telegram_id)
            entry = await self._entry(uow, entry_id, for_update=False)
            if entry.client_id != client.id:
                raise AuthorizationError("Заявка не принадлежит вам.")
            return self._view(entry, await self._service_name(uow, entry.service_id))

    async def list_admin(
        self,
        actor: AdminActor,
        *,
        status: WaitlistStatus | None = None,
        service_id: int | None = None,
        page: PageRequest | None = None,
    ) -> Page[AdminWaitlistView]:
        self._ensure_admin(actor)
        page = page or PageRequest()
        async with self._unit_of_work_factory() as uow:
            entries, total = await uow.waitlist.list_page(
                status=status,
                service_id=service_id,
                limit=page.page_size,
                offset=page.offset,
            )
            views = []
            for entry in entries:
                client = await uow.users.get_by_id(entry.client_id)
                if client is None:
                    continue
                views.append(
                    AdminWaitlistView(
                        **self._view(
                            entry, await self._service_name(uow, entry.service_id)
                        ).model_dump(),
                        client_id=client.id,
                        client_name=client.first_name or "—",
                        client_telegram_id=client.telegram_id,
                    )
                )
            return Page(items=views, total=total, page=page.page, page_size=page.page_size)

    async def get_admin(self, actor: AdminActor, entry_id: int) -> AdminWaitlistView:
        """Return one tenant-scoped request without depending on its list page."""
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as uow:
            entry = await self._entry(uow, entry_id, for_update=False)
            client = await uow.users.get_by_id(entry.client_id)
            if client is None:
                raise EntityNotFoundError("Клиент заявки листа ожидания не найден.")
            return AdminWaitlistView(
                **self._view(entry, await self._service_name(uow, entry.service_id)).model_dump(),
                client_id=client.id,
                client_name=client.first_name or "—",
                client_telegram_id=client.telegram_id,
            )

    async def archive_admin(
        self,
        actor: AdminActor,
        entry_id: int,
        *,
        correlation_id: str | None = None,
    ) -> None:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as uow:
            admin = await uow.users.get_or_create_admin(actor)
            entry = await self._entry(uow, entry_id, for_update=True)
            await self._archive(uow, entry, admin.id, correlation_id)
            await uow.commit()

    async def offer_window(
        self,
        actor: AdminActor,
        entry_id: int,
        window_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        self._ensure_admin(actor)
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as uow:
            await uow.users.get_or_create_admin(actor)
            entry = await self._entry(uow, entry_id, for_update=True)
            if entry.status not in {WaitlistStatus.ACTIVE, WaitlistStatus.MATCHED}:
                raise WaitlistStateError("Заявка уже не активна.")
            window = await uow.windows.get(window_id, for_update=True)
            settings = await self._settings(uow)
            if window is None or window.status is not AvailabilityWindowStatus.OPEN:
                raise WaitlistStateError("Окно уже недоступно.")
            matched = await enqueue_waitlist_matches(
                uow, window, settings, now=current, correlation_id=correlation_id
            )
            await uow.commit()
            return matched > 0

    async def _archive(
        self,
        uow: SqlAlchemyUnitOfWork,
        entry: WaitlistEntry,
        actor_id: int,
        correlation_id: str | None,
    ) -> None:
        if entry.status not in {WaitlistStatus.ACTIVE, WaitlistStatus.MATCHED}:
            raise WaitlistStateError("Заявка уже завершена или отменена.")
        entry.status = WaitlistStatus.CANCELLED
        await uow.waitlist.cancel_unsent(entry.id)
        await uow.audit.add(
            actor_user_id=actor_id,
            action="waitlist.cancelled",
            entity_type="waitlist_entry",
            entity_id=str(entry.id),
            changes={"status": WaitlistStatus.CANCELLED.value},
            correlation_id=correlation_id,
        )

    @staticmethod
    async def _client(uow: SqlAlchemyUnitOfWork, telegram_id: int) -> User:
        client = await uow.users.get_by_telegram_id(telegram_id)
        if client is None or client.privacy_consent_at is None:
            raise PrivacyConsentRequiredError(
                "Сначала примите условия обработки данных через /start."
            )
        if client.is_blocked:
            raise WaitlistStateError("Бот недоступен для этой учётной записи.")
        return client

    @staticmethod
    async def _settings(uow: SqlAlchemyUnitOfWork) -> BusinessSettings:
        settings = await uow.settings.get()
        if settings is None:
            raise RuntimeError("Business settings are missing")
        return settings

    @staticmethod
    async def _entry(
        uow: SqlAlchemyUnitOfWork, entry_id: int, *, for_update: bool
    ) -> WaitlistEntry:
        entry = await uow.waitlist.get(entry_id, for_update=for_update)
        if entry is None:
            raise EntityNotFoundError("Заявка листа ожидания не найдена.")
        return entry

    @staticmethod
    async def _service_name(uow: SqlAlchemyUnitOfWork, service_id: int) -> str:
        service = await uow.services.get(service_id)
        return service.name if service is not None else "Услуга"

    @staticmethod
    def _view(entry: WaitlistEntry, service_name: str) -> WaitlistView:
        return WaitlistView(
            id=entry.id,
            service_id=entry.service_id,
            service_name=service_name,
            date_from=entry.date_from,
            date_to=entry.date_to,
            preferred_dates=entry.preferred_dates,
            preferred_time_from=entry.preferred_time_from,
            preferred_time_to=entry.preferred_time_to,
            status=entry.status,
            expires_at=entry.expires_at,
        )

    def _ensure_admin(self, actor: AdminActor) -> None:
        ensure_admin(actor, self._admin_telegram_ids)

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
