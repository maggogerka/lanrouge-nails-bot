"""Short transactions around rate-limited Telegram broadcast delivery."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.database.models import Broadcast, BroadcastRecipient
from app.domain.enums import BroadcastRecipientStatus, BroadcastStatus
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.broadcast import BroadcastDelivery, BroadcastMediaView

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]

_OPEN = {
    BroadcastRecipientStatus.PENDING,
    BroadcastRecipientStatus.PROCESSING,
    BroadcastRecipientStatus.RETRY,
}


class BroadcastDeliveryService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        lease_seconds: int,
        max_attempts: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def claim_due(
        self,
        worker_id: str,
        *,
        limit: int,
        now: datetime | None = None,
    ) -> list[int]:
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as uow:
            recipients = await uow.broadcasts.claim_due_recipients(
                now=current,
                lease_expired_before=current - timedelta(seconds=self._lease_seconds),
                worker_id=worker_id,
                limit=limit,
            )
            for broadcast_id in {item.broadcast_id for item in recipients}:
                broadcast = await uow.broadcasts.get(broadcast_id, for_update=True)
                if broadcast is not None and broadcast.status in {
                    BroadcastStatus.SCHEDULED,
                    BroadcastStatus.PREPARING,
                }:
                    broadcast.status = BroadcastStatus.SENDING
                    broadcast.started_at = broadcast.started_at or current
            ids = [item.id for item in recipients]
            await uow.commit()
            return ids

    async def prepare_delivery(self, recipient_id: int, worker_id: str) -> BroadcastDelivery | None:
        async with self._unit_of_work_factory() as uow:
            recipient = await self._claimed(uow, recipient_id, worker_id)
            if recipient is None:
                return None
            broadcast = await uow.broadcasts.get(recipient.broadcast_id)
            user = await uow.users.get_by_id(recipient.user_id)
            if broadcast is None or user is None:
                await self._finish(
                    uow, recipient, BroadcastRecipientStatus.FAILED, "context_missing"
                )
                await self._finalize(uow, broadcast)
                await uow.commit()
                return None
            if broadcast.status is not BroadcastStatus.SENDING:
                await self._finish(
                    uow, recipient, BroadcastRecipientStatus.SKIPPED, "broadcast_inactive"
                )
                await self._finalize(uow, broadcast)
                await uow.commit()
                return None
            if user.is_blocked:
                await self._finish(
                    uow, recipient, BroadcastRecipientStatus.BLOCKED, "recipient_blocked"
                )
                await self._finalize(uow, broadcast)
                await uow.commit()
                return None
            if user.marketing_consent_at is None or user.marketing_unsubscribed_at is not None:
                await self._finish(
                    uow,
                    recipient,
                    BroadcastRecipientStatus.UNSUBSCRIBED,
                    "marketing_unsubscribed",
                )
                await self._finalize(uow, broadcast)
                await uow.commit()
                return None
            media = await uow.broadcasts.list_media(broadcast.id)
            return BroadcastDelivery(
                recipient_id=recipient.id,
                broadcast_id=broadcast.id,
                recipient_user_id=user.id,
                recipient_telegram_id=user.telegram_id,
                attempts=recipient.attempts,
                text=broadcast.text,
                button_type=broadcast.button_type,
                button_text=broadcast.button_text,
                button_url=broadcast.button_url,
                media=[
                    BroadcastMediaView(
                        telegram_file_id=item.telegram_file_id,
                        telegram_file_unique_id=item.telegram_file_unique_id,
                        media_type=item.media_type,
                        position=item.position,
                    )
                    for item in media
                ],
            )

    async def mark_sent(
        self, recipient_id: int, worker_id: str, *, telegram_message_id: int
    ) -> bool:
        async with self._unit_of_work_factory() as uow:
            recipient = await self._claimed(uow, recipient_id, worker_id)
            if recipient is None:
                return False
            recipient.sent_at = datetime.now(UTC)
            recipient.telegram_message_id = telegram_message_id
            await self._finish(uow, recipient, BroadcastRecipientStatus.SENT, None)
            await self._finalize_id(uow, recipient.broadcast_id)
            await uow.commit()
            return True

    async def retry(
        self,
        recipient_id: int,
        worker_id: str,
        *,
        delay_seconds: int,
        error_code: str,
    ) -> bool:
        async with self._unit_of_work_factory() as uow:
            recipient = await self._claimed(uow, recipient_id, worker_id)
            if recipient is None:
                return False
            if recipient.attempts >= self._max_attempts:
                await self._finish(
                    uow,
                    recipient,
                    BroadcastRecipientStatus.FAILED,
                    "attempts_exhausted",
                )
                await self._finalize_id(uow, recipient.broadcast_id)
            else:
                recipient.status = BroadcastRecipientStatus.RETRY
                recipient.available_at = datetime.now(UTC) + timedelta(
                    seconds=max(1, delay_seconds)
                )
                recipient.locked_at = None
                recipient.locked_by = None
                recipient.last_error = error_code[:1000]
            await uow.commit()
            return True

    async def mark_blocked(self, recipient_id: int, worker_id: str) -> bool:
        async with self._unit_of_work_factory() as uow:
            recipient = await self._claimed(uow, recipient_id, worker_id)
            if recipient is None:
                return False
            user = await uow.users.get_by_id(recipient.user_id)
            if user is not None:
                await uow.users.mark_blocked(user)
            await self._finish(
                uow, recipient, BroadcastRecipientStatus.BLOCKED, "telegram_forbidden"
            )
            await self._finalize_id(uow, recipient.broadcast_id)
            await uow.commit()
            return True

    async def mark_failed(self, recipient_id: int, worker_id: str, *, error_code: str) -> bool:
        async with self._unit_of_work_factory() as uow:
            recipient = await self._claimed(uow, recipient_id, worker_id)
            if recipient is None:
                return False
            await self._finish(uow, recipient, BroadcastRecipientStatus.FAILED, error_code)
            await self._finalize_id(uow, recipient.broadcast_id)
            await uow.commit()
            return True

    @staticmethod
    async def _claimed(
        uow: SqlAlchemyUnitOfWork, recipient_id: int, worker_id: str
    ) -> BroadcastRecipient | None:
        recipient = await uow.broadcasts.get_recipient(recipient_id, for_update=True)
        if (
            recipient is None
            or recipient.status is not BroadcastRecipientStatus.PROCESSING
            or recipient.locked_by != worker_id
        ):
            return None
        return recipient

    @staticmethod
    async def _finish(
        uow: SqlAlchemyUnitOfWork,
        recipient: BroadcastRecipient,
        status: BroadcastRecipientStatus,
        error: str | None,
    ) -> None:
        recipient.status = status
        recipient.locked_at = None
        recipient.locked_by = None
        recipient.last_error = error[:1000] if error else None
        await uow.session.flush()

    async def _finalize_id(self, uow: SqlAlchemyUnitOfWork, broadcast_id: int) -> None:
        await self._finalize(uow, await uow.broadcasts.get(broadcast_id, for_update=True))

    @staticmethod
    async def _finalize(uow: SqlAlchemyUnitOfWork, broadcast: Broadcast | None) -> None:
        if broadcast is None or broadcast.status is BroadcastStatus.CANCELLED:
            return
        counts = await uow.broadcasts.status_counts(broadcast.id)
        if any(counts.get(status, 0) for status in _OPEN):
            return
        has_failures = any(
            counts.get(status, 0)
            for status in {
                BroadcastRecipientStatus.FAILED,
                BroadcastRecipientStatus.BLOCKED,
            }
        )
        broadcast.status = (
            BroadcastStatus.PARTIALLY_FAILED if has_failures else BroadcastStatus.COMPLETED
        )
        broadcast.finished_at = datetime.now(UTC)

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
