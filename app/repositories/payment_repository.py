"""Business-scoped PostgreSQL persistence for payments, refunds and webhooks."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import Table, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment
from app.database.models.payment import Payment, PaymentWebhookEvent, Refund
from app.database.models.user import User
from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.repositories.scoped import TenantScopedRepository


class PaymentRepository(TenantScopedRepository):
    """Queries that always include the current business boundary."""

    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def add(self, payment: Payment) -> Payment:
        self._require_business(payment.business_id)
        self._session.add(payment)
        await self._session.flush()
        return payment

    async def get(self, payment_id: int, *, for_update: bool = False) -> Payment | None:
        statement = select(Payment).where(
            Payment.id == payment_id,
            Payment.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_idempotency_key(
        self, idempotency_key: str, *, for_update: bool = False
    ) -> Payment | None:
        statement = select(Payment).where(
            Payment.business_id == self.business_id,
            Payment.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_provider_id(
        self,
        provider: PaymentMode,
        provider_payment_id: str,
        *,
        for_update: bool = False,
    ) -> Payment | None:
        statement = select(Payment).where(
            Payment.business_id == self.business_id,
            Payment.provider == provider,
            Payment.provider_payment_id == provider_payment_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_latest_for_appointment(
        self, appointment_id: int, *, for_update: bool = False
    ) -> Payment | None:
        statement = (
            select(Payment)
            .where(
                Payment.business_id == self.business_id,
                Payment.appointment_id == appointment_id,
            )
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def list_recent(
        self,
        *,
        statuses: Collection[PaymentStatus] | None = None,
        staff_member_id: int | None = None,
        limit: int = 30,
    ) -> list[Payment]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        statement = (
            select(Payment)
            .where(Payment.business_id == self.business_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(limit)
        )
        if statuses is not None:
            if not statuses:
                return []
            statement = statement.where(Payment.status.in_(statuses))
        if staff_member_id is not None:
            statement = statement.join(
                Appointment,
                Appointment.id == Payment.appointment_id,
            ).where(
                Appointment.business_id == self.business_id,
                Appointment.staff_member_id == staff_member_id,
            )
        return list(await self._session.scalars(statement))

    async def list_recent_with_context(
        self,
        *,
        statuses: Collection[PaymentStatus],
        staff_member_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Payment, Appointment, User]]:
        """Return one bounded, tenant-scoped staff-panel projection query."""

        if not statuses:
            return []
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must not be negative")
        statement = (
            select(Payment, Appointment, User)
            .join(
                Appointment,
                (Appointment.id == Payment.appointment_id)
                & (Appointment.business_id == self.business_id),
            )
            .join(User, User.id == Appointment.client_id)
            .where(
                Payment.business_id == self.business_id,
                Payment.status.in_(statuses),
            )
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if staff_member_id is not None:
            statement = statement.where(Appointment.staff_member_id == staff_member_id)
        rows = await self._session.execute(statement)
        return [(row[0], row[1], row[2]) for row in rows.all()]

    async def count_with_context(
        self,
        *,
        statuses: Collection[PaymentStatus],
        staff_member_id: int | None = None,
    ) -> int:
        if not statuses:
            return 0
        statement = select(func.count(Payment.id)).where(
            Payment.business_id == self.business_id,
            Payment.status.in_(statuses),
        )
        if staff_member_id is not None:
            statement = statement.join(
                Appointment,
                (Appointment.id == Payment.appointment_id)
                & (Appointment.business_id == self.business_id),
            ).where(Appointment.staff_member_id == staff_member_id)
        return int((await self._session.scalar(statement)) or 0)

    async def get_with_context(
        self,
        payment_id: int,
        *,
        staff_member_id: int | None = None,
    ) -> tuple[Payment, Appointment, User] | None:
        statement = (
            select(Payment, Appointment, User)
            .join(
                Appointment,
                (Appointment.id == Payment.appointment_id)
                & (Appointment.business_id == self.business_id),
            )
            .join(User, User.id == Appointment.client_id)
            .where(
                Payment.id == payment_id,
                Payment.business_id == self.business_id,
            )
        )
        if staff_member_id is not None:
            statement = statement.where(Appointment.staff_member_id == staff_member_id)
        row = (await self._session.execute(statement)).one_or_none()
        return (row[0], row[1], row[2]) if row is not None else None

    async def add_refund(self, refund: Refund) -> Refund:
        self._require_business(refund.business_id)
        self._session.add(refund)
        await self._session.flush()
        return refund

    async def add_refund_if_absent(self, refund: Refund) -> tuple[Refund, bool]:
        """Insert by business idempotency key without aborting a concurrent transaction."""

        self._require_business(refund.business_id)
        values = {
            "business_id": refund.business_id,
            "payment_id": refund.payment_id,
            "provider": refund.provider,
            "provider_refund_id": refund.provider_refund_id,
            "idempotency_key": refund.idempotency_key,
            "amount": refund.amount,
            "currency": refund.currency,
            "status": refund.status,
            "reason_code": refund.reason_code,
            "metadata": refund.safe_metadata,
            "requested_by_user_id": refund.requested_by_user_id,
            "succeeded_at": refund.succeeded_at,
            "failed_at": refund.failed_at,
            "attempts": refund.attempts,
            "last_error_code": refund.last_error_code,
            "correlation_id": refund.correlation_id,
        }
        table = cast(Table, Refund.__table__)
        inserted_id = await self._session.scalar(
            pg_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[table.c.business_id, table.c.idempotency_key])
            .returning(table.c.id)
        )
        if inserted_id is None:
            existing = await self.get_refund_by_idempotency_key(
                refund.idempotency_key,
                for_update=True,
            )
            if existing is None:
                raise RuntimeError("refund dedupe conflict did not return the existing row")
            return existing, False
        inserted = (
            await self._session.scalars(
                select(Refund).where(
                    Refund.id == inserted_id,
                    Refund.business_id == self.business_id,
                )
            )
        ).one()
        return inserted, True

    async def get_refund(self, refund_id: int, *, for_update: bool = False) -> Refund | None:
        statement = select(Refund).where(
            Refund.id == refund_id,
            Refund.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_refund_by_idempotency_key(
        self, idempotency_key: str, *, for_update: bool = False
    ) -> Refund | None:
        statement = select(Refund).where(
            Refund.business_id == self.business_id,
            Refund.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_refund_by_provider_id(
        self,
        provider: PaymentMode,
        provider_refund_id: str,
        *,
        for_update: bool = False,
    ) -> Refund | None:
        statement = select(Refund).where(
            Refund.business_id == self.business_id,
            Refund.provider == provider,
            Refund.provider_refund_id == provider_refund_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def sum_pending_refunds(self, payment_id: int) -> Decimal:
        """Return amount reserved by in-flight refunds under a caller-held payment lock."""

        amount = await self._session.scalar(
            select(func.coalesce(func.sum(Refund.amount), Decimal("0.00"))).where(
                Refund.business_id == self.business_id,
                Refund.payment_id == payment_id,
                Refund.status == RefundStatus.PENDING,
            )
        )
        return Decimal(amount or 0).quantize(Decimal("0.01"))

    async def get_webhook_by_event_key(
        self,
        provider: PaymentMode,
        event_key: str,
        *,
        for_update: bool = False,
    ) -> PaymentWebhookEvent | None:
        statement = select(PaymentWebhookEvent).where(
            PaymentWebhookEvent.business_id == self.business_id,
            PaymentWebhookEvent.provider == provider,
            PaymentWebhookEvent.event_key == event_key,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def add_webhook_if_absent(
        self, event: PaymentWebhookEvent
    ) -> tuple[PaymentWebhookEvent, bool]:
        """Deduplicate atomically without poisoning the transaction on a retry race."""

        self._require_business(event.business_id)
        values = {
            "business_id": event.business_id,
            "payment_id": event.payment_id,
            "provider": event.provider,
            "event_key": event.event_key,
            "event_type": event.event_type,
            "provider_object_id": event.provider_object_id,
            "provider_payment_id": event.provider_payment_id,
            "payload_sha256": event.payload_sha256,
            "status": event.status,
            "received_at": event.received_at,
            "processed_at": event.processed_at,
            "expires_at": event.expires_at,
            "attempts": event.attempts,
            "last_error_code": event.last_error_code,
            "correlation_id": event.correlation_id,
        }
        inserted_id = await self._session.scalar(
            pg_insert(PaymentWebhookEvent)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    PaymentWebhookEvent.business_id,
                    PaymentWebhookEvent.provider,
                    PaymentWebhookEvent.event_key,
                ]
            )
            .returning(PaymentWebhookEvent.id)
        )
        if inserted_id is None:
            existing = await self.get_webhook_by_event_key(event.provider, event.event_key)
            if existing is None:
                raise RuntimeError("webhook dedupe conflict did not return the existing row")
            return existing, False
        inserted = (
            await self._session.scalars(
                select(PaymentWebhookEvent).where(
                    PaymentWebhookEvent.id == inserted_id,
                    PaymentWebhookEvent.business_id == self.business_id,
                )
            )
        ).one()
        return inserted, True

    async def delete_expired_webhooks(self, *, now: datetime, limit: int = 1000) -> int:
        """Keep the dedupe inbox bounded while avoiding contention with processors."""

        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        expired_ids = (
            select(PaymentWebhookEvent.id)
            .where(
                PaymentWebhookEvent.business_id == self.business_id,
                PaymentWebhookEvent.expires_at <= now,
            )
            .order_by(PaymentWebhookEvent.expires_at, PaymentWebhookEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(
            delete(PaymentWebhookEvent).where(PaymentWebhookEvent.id.in_(expired_ids))
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def _require_business(self, business_id: int) -> None:
        if business_id != self.business_id:
            raise ValueError("entity belongs to another business")
