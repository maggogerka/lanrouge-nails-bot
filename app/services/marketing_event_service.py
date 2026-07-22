"""Minimal internal callback tracking for delivered broadcasts."""

from __future__ import annotations

from collections.abc import Callable

from app.database.models import MarketingEvent
from app.domain.enums import BroadcastRecipientStatus, MarketingEventType
from app.domain.errors import AuthorizationError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class MarketingEventService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def record(
        self,
        actor: ClientActor,
        broadcast_id: int,
        event_type: MarketingEventType,
    ) -> None:
        async with self._unit_of_work_factory() as uow:
            user = await uow.users.get_by_telegram_id(actor.telegram_id)
            if user is None:
                raise AuthorizationError("Получатель рассылки не найден.")
            recipient = await uow.broadcasts.get_recipient_for_user(broadcast_id, user.id)
            if recipient is None or recipient.status is not BroadcastRecipientStatus.SENT:
                raise AuthorizationError("Это действие недоступно.")
            await uow.broadcasts.add_event(
                MarketingEvent(
                    user_id=user.id,
                    broadcast_id=broadcast_id,
                    event_type=event_type,
                    event_data={},
                )
            )
            await uow.commit()
