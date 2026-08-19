"""Short notification-queue transactions surrounding external Telegram I/O."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.database.models import NotificationJob
from app.domain.appointments import ACTIVE_APPOINTMENT_STATUSES
from app.domain.enums import (
    AppointmentStatus,
    ManualPaymentStatus,
    NotificationJobStatus,
    NotificationType,
    PaymentMode,
    PaymentStatus,
)
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.features import FeatureName
from app.schemas.notification import NotificationDelivery
from app.services.feature_guard import is_feature_enabled

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class NotificationService:
    """Claim with leases, revalidate, and finalize persistent reminder jobs."""

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
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            jobs = await unit_of_work.notifications.claim_due(
                now=current_time,
                lease_expired_before=current_time - timedelta(seconds=self._lease_seconds),
                worker_id=worker_id,
                limit=limit,
            )
            job_ids = [job.id for job in jobs]
            await unit_of_work.commit()
            return job_ids

    async def prepare_delivery(
        self,
        job_id: int,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> NotificationDelivery | None:
        """Recheck appointment and recipient immediately before Telegram I/O."""

        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            job = await self._claimed_job(unit_of_work, job_id, worker_id)
            if job is None:
                return None
            if job.notification_type is NotificationType.REPEAT_BOOKING_REMINDER:
                await self._cancel_job(unit_of_work, job, "feature_removed")
                await unit_of_work.commit()
                return None
            required_feature = self._required_feature(job.notification_type)
            if not await is_feature_enabled(unit_of_work, required_feature):
                await self._cancel_job(unit_of_work, job, "feature_disabled")
                await unit_of_work.commit()
                return None
            appointment = await unit_of_work.appointments.get(job.appointment_id)
            recipient = await unit_of_work.users.get_by_id(job.recipient_user_id)
            if appointment is None or recipient is None:
                await self._fail_job(unit_of_work, job, "delivery_context_missing")
                await unit_of_work.commit()
                return None
            payment = None
            if job.notification_type in {
                NotificationType.PAYMENT_DUE_CLIENT,
                NotificationType.PAYMENT_REVIEW_STAFF,
            }:
                payment = await unit_of_work.payments.get_latest_for_appointment(job.appointment_id)
                client_due = (
                    job.notification_type is NotificationType.PAYMENT_DUE_CLIENT
                    and appointment.status is AppointmentStatus.PENDING_PAYMENT
                    and payment is not None
                    and payment.provider is PaymentMode.MANUAL
                    and payment.status is PaymentStatus.PENDING
                    and payment.manual_status is ManualPaymentStatus.AWAITING_PAYMENT
                    and payment.expires_at is not None
                    and payment.expires_at > current_time
                )
                staff_review = (
                    job.notification_type is NotificationType.PAYMENT_REVIEW_STAFF
                    and appointment.status is AppointmentStatus.PENDING_MANUAL_CONFIRMATION
                    and payment is not None
                    and payment.provider is PaymentMode.MANUAL
                    and payment.status is PaymentStatus.PENDING
                    and payment.manual_status
                    in {
                        ManualPaymentStatus.CLIENT_REPORTED,
                        ManualPaymentStatus.REVIEW_PENDING,
                    }
                )
                if not client_due and not staff_review:
                    await self._cancel_job(unit_of_work, job, "payment_not_actionable")
                    await unit_of_work.commit()
                    return None
            elif job.notification_type is NotificationType.REVIEW_REQUEST:
                settings = await unit_of_work.settings.get()
                if (
                    appointment.status is not AppointmentStatus.COMPLETED
                    or settings is None
                    or not settings.reviews_enabled
                    or await unit_of_work.reviews.get_for_appointment(appointment.id) is not None
                ):
                    await self._cancel_job(unit_of_work, job, "review_not_actionable")
                    await unit_of_work.commit()
                    return None
            elif appointment.status not in ACTIVE_APPOINTMENT_STATUSES:
                await self._cancel_job(unit_of_work, job, "appointment_inactive")
                await unit_of_work.commit()
                return None
            if recipient.is_blocked:
                await self._fail_job(unit_of_work, job, "recipient_blocked")
                await unit_of_work.commit()
                return None
            window = await unit_of_work.windows.get(appointment.window_id)
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            settings = await unit_of_work.settings.get()
            if window is None or client is None or settings is None:
                await self._fail_job(unit_of_work, job, "delivery_context_missing")
                await unit_of_work.commit()
                return None
            if (
                job.notification_type
                in {
                    NotificationType.CLIENT_REMINDER,
                    NotificationType.ADMIN_REMINDER,
                }
                and window.start_at <= current_time
            ):
                await self._cancel_job(unit_of_work, job, "appointment_started")
                await unit_of_work.commit()
                return None
            return NotificationDelivery(
                job_id=job.id,
                appointment_id=appointment.id,
                recipient_user_id=recipient.id,
                recipient_telegram_id=recipient.telegram_id,
                notification_type=job.notification_type,
                offset_minutes=job.offset_minutes,
                attempts=job.attempts,
                service_name=appointment.service_name_snapshot,
                start_at=window.start_at,
                timezone=settings.timezone,
                address=appointment.address_snapshot or "Адрес не указан",
                map_url=appointment.map_url_snapshot,
                master_telegram_url=appointment.master_contact_url_snapshot,
                client_name=client.first_name or "—",
                client_phone=client.phone,
                payment_id=payment.id if payment is not None else None,
            )

    async def mark_sent(
        self,
        job_id: int,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            job = await self._claimed_job(unit_of_work, job_id, worker_id)
            if job is None:
                return False
            job.status = NotificationJobStatus.SENT
            job.sent_at = current_time
            job.locked_at = None
            job.locked_by = None
            job.last_error = None
            await unit_of_work.session.flush()
            await unit_of_work.commit()
            return True

    async def retry(
        self,
        job_id: int,
        worker_id: str,
        *,
        delay_seconds: int,
        error_code: str,
        now: datetime | None = None,
    ) -> bool:
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            job = await self._claimed_job(unit_of_work, job_id, worker_id)
            if job is None:
                return False
            if job.attempts >= self._max_attempts:
                await self._fail_job(unit_of_work, job, "attempts_exhausted")
            else:
                job.status = NotificationJobStatus.PENDING
                job.available_at = current_time + timedelta(seconds=max(1, delay_seconds))
                job.locked_at = None
                job.locked_by = None
                job.last_error = error_code[:1000]
                await unit_of_work.session.flush()
            await unit_of_work.commit()
            return True

    async def mark_recipient_blocked(
        self,
        job_id: int,
        worker_id: str,
    ) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            job = await self._claimed_job(unit_of_work, job_id, worker_id)
            if job is None:
                return False
            recipient = await unit_of_work.users.get_by_id(job.recipient_user_id)
            if recipient is not None:
                await unit_of_work.users.mark_blocked(recipient)
            await self._fail_job(unit_of_work, job, "telegram_forbidden")
            await unit_of_work.commit()
            return True

    async def mark_permanent_failure(
        self,
        job_id: int,
        worker_id: str,
        *,
        error_code: str,
    ) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            job = await self._claimed_job(unit_of_work, job_id, worker_id)
            if job is None:
                return False
            await self._fail_job(unit_of_work, job, error_code)
            await unit_of_work.commit()
            return True

    @staticmethod
    async def _claimed_job(
        unit_of_work: SqlAlchemyUnitOfWork,
        job_id: int,
        worker_id: str,
    ) -> NotificationJob | None:
        job = await unit_of_work.notifications.get(job_id, for_update=True)
        if (
            job is None
            or job.status is not NotificationJobStatus.PROCESSING
            or job.locked_by != worker_id
        ):
            return None
        return job

    @staticmethod
    async def _cancel_job(
        unit_of_work: SqlAlchemyUnitOfWork,
        job: NotificationJob,
        reason: str,
    ) -> None:
        job.status = NotificationJobStatus.CANCELLED
        job.locked_at = None
        job.locked_by = None
        job.last_error = reason
        await unit_of_work.session.flush()

    @staticmethod
    async def _fail_job(
        unit_of_work: SqlAlchemyUnitOfWork,
        job: NotificationJob,
        error_code: str,
    ) -> None:
        job.status = NotificationJobStatus.FAILED
        job.locked_at = None
        job.locked_by = None
        job.last_error = error_code[:1000]
        await unit_of_work.session.flush()

    @staticmethod
    def _required_feature(notification_type: NotificationType) -> FeatureName:
        if notification_type is NotificationType.REVIEW_REQUEST:
            return FeatureName.REVIEWS
        if notification_type is NotificationType.REPEAT_BOOKING_REMINDER:
            return FeatureName.REPEAT_BOOKING
        if notification_type in {
            NotificationType.PAYMENT_DUE_CLIENT,
            NotificationType.PAYMENT_REVIEW_STAFF,
        }:
            return FeatureName.PREPAYMENT
        return FeatureName.REMINDERS

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
