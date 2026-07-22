"""Repeat the latest completed service using current catalog and booking rules."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models import User
from app.domain.errors import PrivacyConsentRequiredError, RepeatBookingStateError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor
from app.schemas.repeat_booking import RepeatBookingOffer

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class RepeatBookingService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def get_offer(self, actor: ClientActor) -> RepeatBookingOffer:
        async with self._unit_of_work_factory() as uow:
            client = await self._client(uow, actor.telegram_id)
            previous = await uow.appointments.latest_completed_for_client(client.id)
            if previous is None:
                raise RepeatBookingStateError(
                    "Пока нет завершённой записи, которую можно повторить."
                )
            service = await uow.services.get(previous.service_id)
            settings = await uow.settings.get()
            if settings is None:
                raise RuntimeError("Business settings are missing")
            return RepeatBookingOffer(
                previous_appointment_id=previous.id,
                service_id=previous.service_id,
                service_name=service.name
                if service is not None
                else previous.service_name_snapshot,
                previous_price=previous.price_snapshot,
                current_price=service.price if service is not None else None,
                service_active=bool(service is not None and service.is_active),
                master_telegram_url=settings.master_telegram_url,
            )

    async def opt_out(
        self,
        actor: ClientActor,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> None:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        async with self._unit_of_work_factory() as uow:
            client = await self._client(uow, actor.telegram_id, for_update=True)
            client.repeat_booking_opt_out_at = current.astimezone(UTC)
            await uow.audit.add(
                actor_user_id=client.id,
                action="repeat_booking.opted_out",
                entity_type="user",
                entity_id=str(client.id),
                changes={"opted_out_at": current.astimezone(UTC).isoformat()},
                correlation_id=correlation_id,
            )
            await uow.commit()

    @staticmethod
    async def _client(
        uow: SqlAlchemyUnitOfWork, telegram_id: int, *, for_update: bool = False
    ) -> User:
        client = await uow.users.get_by_telegram_id(telegram_id, for_update=for_update)
        if client is None or client.privacy_consent_at is None:
            raise PrivacyConsentRequiredError(
                "Сначала примите условия обработки данных через /start."
            )
        return client
