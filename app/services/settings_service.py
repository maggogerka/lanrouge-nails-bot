"""Authorized viewing and mutation of core business rules."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models import NotificationJob
from app.domain.enums import NotificationJobStatus, NotificationType
from app.domain.notifications import future_reminder_schedules
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.service import AdminActor
from app.schemas.settings import BusinessSettingsPatch, BusinessSettingsView
from app.services.appointment_common import ensure_admin

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class SettingsService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def get(self, actor: AdminActor) -> BusinessSettingsView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            return BusinessSettingsView.model_validate(settings)

    async def update(
        self,
        actor: AdminActor,
        patch: BusinessSettingsPatch,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> BusinessSettingsView:
        ensure_admin(actor, self._admin_telegram_ids)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            settings = await unit_of_work.settings.get(for_update=True)
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            changes = patch.model_dump(exclude_unset=True)
            field, new_value = next(iter(changes.items()))
            old_value = getattr(settings, field)
            setattr(settings, field, new_value)
            settings.version += 1
            if field == "reminder_offsets_minutes":
                await self._rebuild_future_reminders(
                    unit_of_work,
                    offsets_minutes=new_value,
                    now=current_time,
                )
            await unit_of_work.session.flush()
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="business_settings.updated",
                entity_type="business_settings",
                entity_id="1",
                changes={
                    field: {"before": old_value, "after": new_value},
                    "version": settings.version,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return BusinessSettingsView.model_validate(settings)

    async def _rebuild_future_reminders(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        *,
        offsets_minutes: list[int],
        now: datetime,
    ) -> None:
        admin_users = await unit_of_work.users.list_by_telegram_ids(self._admin_telegram_ids)
        active_admin_ids = [user.id for user in admin_users if not user.is_blocked]
        rows = await unit_of_work.appointments.list_future_active(now)
        for appointment, window in rows:
            client = await unit_of_work.users.get_by_id(appointment.client_id)
            if client is None:
                raise RuntimeError("Appointment client is missing")
            schedules = future_reminder_schedules(
                start_at=window.start_at,
                now=now,
                offsets_minutes=offsets_minutes,
                client_user_id=client.id,
                admin_user_ids=active_admin_ids,
            )
            if client.is_blocked:
                schedules = [
                    schedule
                    for schedule in schedules
                    if schedule.notification_type is not NotificationType.CLIENT_REMINDER
                ]
            existing = await unit_of_work.notifications.list_for_appointment(appointment.id)
            existing_by_key = {
                (job.recipient_user_id, job.notification_type, job.offset_minutes): job
                for job in existing
            }
            desired_keys = {
                (
                    schedule.recipient_user_id,
                    schedule.notification_type,
                    schedule.offset_minutes,
                )
                for schedule in schedules
            }
            for key, job in existing_by_key.items():
                if key not in desired_keys and job.status in (
                    NotificationJobStatus.PENDING,
                    NotificationJobStatus.PROCESSING,
                ):
                    job.status = NotificationJobStatus.CANCELLED
                    job.locked_at = None
                    job.locked_by = None

            new_jobs: list[NotificationJob] = []
            for schedule in schedules:
                key = (
                    schedule.recipient_user_id,
                    schedule.notification_type,
                    schedule.offset_minutes,
                )
                existing_job = existing_by_key.get(key)
                if existing_job is None:
                    new_jobs.append(
                        NotificationJob(
                            appointment_id=appointment.id,
                            recipient_user_id=schedule.recipient_user_id,
                            notification_type=schedule.notification_type,
                            offset_minutes=schedule.offset_minutes,
                            scheduled_at=schedule.scheduled_at,
                            available_at=schedule.scheduled_at,
                        )
                    )
                elif existing_job.status is not NotificationJobStatus.SENT:
                    existing_job.scheduled_at = schedule.scheduled_at
                    existing_job.available_at = schedule.scheduled_at
                    existing_job.status = NotificationJobStatus.PENDING
                    existing_job.attempts = 0
                    existing_job.locked_at = None
                    existing_job.locked_by = None
                    existing_job.sent_at = None
                    existing_job.last_error = None
            if new_jobs:
                await unit_of_work.notifications.add_all(new_jobs)

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
