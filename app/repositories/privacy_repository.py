"""Tenant-scoped persistence primitives for privacy and acquisition services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatusHistory
from app.database.models.appointment_reference import AppointmentReferenceMedia
from app.database.models.broadcast import BroadcastRecipient
from app.database.models.business import (
    Business,
    BusinessClient,
    StaffInvitation,
    StaffMember,
)
from app.database.models.crm import ClientNote, ConsentHistory, UserClientTag
from app.database.models.master_profile import MasterProfile, MasterPublicLink
from app.database.models.notification import NotificationJob
from app.database.models.payment import Payment, Refund
from app.database.models.privacy import (
    AcquisitionSource,
    ClientAcquisitionAttribution,
    DataDeletionRequest,
    DataDeletionRequestEvent,
)
from app.database.models.review import Review, ReviewRevision
from app.database.models.user import User
from app.database.models.waitlist import WaitlistEntry, WaitlistNotification
from app.domain.enums import (
    AppointmentStatus,
    BroadcastRecipientStatus,
    ConsentType,
    DataDeletionRequestStatus,
    NotificationJobStatus,
    UserRole,
    WaitlistNotificationStatus,
    WaitlistStatus,
)

_OPEN_DELETION_STATUSES = (
    DataDeletionRequestStatus.REQUESTED,
    DataDeletionRequestStatus.IN_REVIEW,
    DataDeletionRequestStatus.APPROVED,
    DataDeletionRequestStatus.PROCESSING,
    DataDeletionRequestStatus.FAILED,
)


@dataclass(frozen=True, slots=True)
class AnonymizationBlockers:
    """PII-free preflight result used to fail closed before any mutation."""

    active_staff_memberships: int = 0
    other_active_business_memberships: int = 0
    active_staff_roles: tuple[str, ...] = ()
    bootstrap_owner: bool = False

    @property
    def error_codes(self) -> tuple[str, ...]:
        codes: list[str] = []
        if self.bootstrap_owner:
            codes.append("bootstrap_owner")
        elif self.active_staff_roles:
            codes.extend(f"active_staff_role.{role}" for role in self.active_staff_roles)
        elif self.active_staff_memberships:
            codes.append("active_staff_membership")
        if self.other_active_business_memberships:
            codes.append("other_active_business_membership")
        return tuple(codes)


@dataclass(frozen=True, slots=True)
class AnonymizationMutationCounts:
    """Only non-sensitive counters may cross the repository boundary."""

    identities_anonymized: int
    notes_anonymized: int
    comments_anonymized: int
    reviews_anonymized: int
    reference_links_removed: int
    deliveries_cancelled: int
    appointment_snapshots_retained: int
    financial_snapshots_retained: int


@dataclass(frozen=True, slots=True)
class AcquisitionMetricRow:
    """PII-free first-touch funnel counters for one source."""

    source: AcquisitionSource
    clients_arrived: int
    clients_started_booking: int
    clients_completed_booking: int
    repeat_clients: int


class PrivacyRepository:
    """Repository that requires an explicit tenant and supports row-locked workflows."""

    def __init__(self, session: AsyncSession, *, business_id: int) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self._session = session
        self.business_id = business_id

    async def get_business(self) -> Business | None:
        return await self._session.get(Business, self.business_id)

    async def get_client(
        self,
        business_client_id: int,
        *,
        for_update: bool = False,
    ) -> BusinessClient | None:
        statement = select(BusinessClient).where(
            BusinessClient.id == business_client_id,
            BusinessClient.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        rows = await self._session.scalars(statement)
        return rows.first()

    async def get_client_by_user(
        self,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> BusinessClient | None:
        statement = select(BusinessClient).where(
            BusinessClient.user_id == user_id,
            BusinessClient.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        rows = await self._session.scalars(statement)
        return rows.first()

    async def latest_consent(
        self,
        user_id: int,
        consent_type: ConsentType,
    ) -> ConsentHistory | None:
        rows = await self._session.scalars(
            select(ConsentHistory)
            .where(
                ConsentHistory.business_id == self.business_id,
                ConsentHistory.user_id == user_id,
                ConsentHistory.consent_type == consent_type,
            )
            .order_by(ConsentHistory.created_at.desc(), ConsentHistory.id.desc())
            .limit(1)
        )
        return rows.first()

    async def list_acquisition_sources(
        self,
        *,
        active_only: bool = True,
    ) -> tuple[AcquisitionSource, ...]:
        statement = select(AcquisitionSource).where(
            AcquisitionSource.business_id == self.business_id
        )
        if active_only:
            statement = statement.where(AcquisitionSource.is_active.is_(True))
        rows = await self._session.scalars(
            statement.order_by(AcquisitionSource.display_name, AcquisitionSource.id)
        )
        return tuple(rows.all())

    async def acquisition_metrics(self) -> tuple[AcquisitionMetricRow, ...]:
        """Aggregate a first-touch booking funnel without returning client identities."""

        per_client = (
            select(
                ClientAcquisitionAttribution.first_source_id.label("source_id"),
                ClientAcquisitionAttribution.business_client_id.label("business_client_id"),
                func.count(Appointment.id).label("booking_count"),
                func.count(Appointment.id)
                .filter(Appointment.status == AppointmentStatus.COMPLETED)
                .label("completed_count"),
            )
            .join(
                BusinessClient,
                BusinessClient.id == ClientAcquisitionAttribution.business_client_id,
            )
            .outerjoin(
                Appointment,
                (Appointment.business_id == self.business_id)
                & (Appointment.client_id == BusinessClient.user_id),
            )
            .where(ClientAcquisitionAttribution.business_id == self.business_id)
            .group_by(
                ClientAcquisitionAttribution.first_source_id,
                ClientAcquisitionAttribution.business_client_id,
            )
            .subquery()
        )
        statement = (
            select(
                AcquisitionSource,
                func.count(per_client.c.business_client_id).label("clients_arrived"),
                func.count(per_client.c.business_client_id)
                .filter(per_client.c.booking_count > 0)
                .label("clients_started_booking"),
                func.count(per_client.c.business_client_id)
                .filter(per_client.c.completed_count > 0)
                .label("clients_completed_booking"),
                func.count(per_client.c.business_client_id)
                .filter(per_client.c.completed_count > 1)
                .label("repeat_clients"),
            )
            .outerjoin(per_client, per_client.c.source_id == AcquisitionSource.id)
            .where(
                AcquisitionSource.business_id == self.business_id,
                AcquisitionSource.is_active.is_(True),
            )
            .group_by(AcquisitionSource.id)
            .order_by(AcquisitionSource.display_name, AcquisitionSource.id)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple(
            AcquisitionMetricRow(
                source=row[0],
                clients_arrived=int(row[1]),
                clients_started_booking=int(row[2]),
                clients_completed_booking=int(row[3]),
                repeat_clients=int(row[4]),
            )
            for row in rows
        )

    async def add_consent(self, entry: ConsentHistory) -> ConsentHistory:
        self._require_business(entry.business_id)
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get_open_deletion_request(
        self,
        business_client_id: int,
        *,
        for_update: bool = False,
    ) -> DataDeletionRequest | None:
        statement = (
            select(DataDeletionRequest)
            .where(
                DataDeletionRequest.business_id == self.business_id,
                DataDeletionRequest.business_client_id == business_client_id,
                DataDeletionRequest.status.in_(_OPEN_DELETION_STATUSES),
            )
            .order_by(DataDeletionRequest.requested_at.desc(), DataDeletionRequest.id.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        rows = await self._session.scalars(statement)
        return rows.first()

    async def get_deletion_request(
        self,
        request_id: int,
        *,
        for_update: bool = False,
    ) -> DataDeletionRequest | None:
        statement = select(DataDeletionRequest).where(
            DataDeletionRequest.id == request_id,
            DataDeletionRequest.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        rows = await self._session.scalars(statement)
        return rows.first()

    async def list_deletion_requests(
        self,
        *,
        statuses: tuple[DataDeletionRequestStatus, ...] | None = None,
        limit: int = 50,
    ) -> tuple[DataDeletionRequest, ...]:
        """List requests for this repository's tenant only, newest first."""

        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        statement = select(DataDeletionRequest).where(
            DataDeletionRequest.business_id == self.business_id
        )
        if statuses:
            statement = statement.where(DataDeletionRequest.status.in_(statuses))
        rows = await self._session.scalars(
            statement.order_by(
                DataDeletionRequest.requested_at.desc(),
                DataDeletionRequest.id.desc(),
            ).limit(limit)
        )
        return tuple(rows.all())

    async def get_user(self, user_id: int, *, for_update: bool = False) -> User | None:
        statement = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update()
        rows = await self._session.scalars(statement)
        return rows.first()

    async def anonymization_blockers(self, user_id: int) -> AnonymizationBlockers:
        """Detect identities whose global profile cannot safely be anonymized."""

        active_staff_rows = (
            await self._session.execute(
                select(StaffMember.role, StaffMember.is_bootstrap_owner).where(
                    StaffMember.user_id == user_id,
                    StaffMember.is_active.is_(True),
                )
            )
        ).all()
        other_clients = await self._session.scalar(
            select(func.count())
            .select_from(BusinessClient)
            .where(
                BusinessClient.user_id == user_id,
                BusinessClient.business_id != self.business_id,
                BusinessClient.is_active.is_(True),
            )
        )
        return AnonymizationBlockers(
            active_staff_memberships=len(active_staff_rows),
            other_active_business_memberships=int(other_clients or 0),
            active_staff_roles=tuple(sorted({row.role.value for row in active_staff_rows})),
            bootstrap_owner=any(row.is_bootstrap_owner for row in active_staff_rows),
        )

    async def anonymize_client_data(
        self,
        *,
        business_client_id: int,
        user_id: int,
        changed_at: datetime,
    ) -> AnonymizationMutationCounts:
        """Anonymize mutable PII while retaining legal booking/accounting snapshots.

        Callers must lock the request, membership, and user and run the blocker
        preflight before invoking this method. All statements remain tenant scoped.
        """

        client = await self.get_client(business_client_id, for_update=True)
        user = await self.get_user(user_id, for_update=True)
        if client is None or client.user_id != user_id or user is None:
            raise ValueError("privacy subject does not belong to this business")

        appointment_ids = select(Appointment.id).where(
            Appointment.business_id == self.business_id,
            Appointment.client_id == user_id,
        )
        review_ids = select(Review.id).where(
            Review.business_id == self.business_id,
            Review.client_id == user_id,
        )
        waitlist_ids = select(WaitlistEntry.id).where(
            WaitlistEntry.business_id == self.business_id,
            WaitlistEntry.client_id == user_id,
        )
        payment_ids = select(Payment.id).where(
            Payment.business_id == self.business_id,
            Payment.appointment_id.in_(appointment_ids),
        )

        appointments_retained = int(
            await self._session.scalar(
                select(func.count())
                .select_from(Appointment)
                .where(
                    Appointment.business_id == self.business_id,
                    Appointment.client_id == user_id,
                )
            )
            or 0
        )
        payments_retained = int(
            await self._session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(
                    Payment.business_id == self.business_id,
                    Payment.appointment_id.in_(appointment_ids),
                )
            )
            or 0
        )
        refunds_retained = int(
            await self._session.scalar(
                select(func.count())
                .select_from(Refund)
                .where(
                    Refund.business_id == self.business_id,
                    Refund.payment_id.in_(payment_ids),
                )
            )
            or 0
        )

        notes = self._rowcount(
            await self._session.execute(
                update(ClientNote)
                .where(
                    ClientNote.business_id == self.business_id,
                    ClientNote.client_id == user_id,
                )
                .values(text="[anonymized]", archived_at=changed_at)
            )
        )
        appointment_comments = self._rowcount(
            await self._session.execute(
                update(Appointment)
                .where(
                    Appointment.business_id == self.business_id,
                    Appointment.client_id == user_id,
                    (Appointment.client_comment.is_not(None))
                    | (Appointment.cancellation_reason.is_not(None)),
                )
                .values(client_comment=None, cancellation_reason=None)
            )
        )
        appointment_history_comments = self._rowcount(
            await self._session.execute(
                update(AppointmentStatusHistory)
                .where(
                    AppointmentStatusHistory.appointment_id.in_(appointment_ids),
                    AppointmentStatusHistory.reason.is_not(None),
                )
                .values(reason="[anonymized]")
            )
        )
        review_revisions = self._rowcount(
            await self._session.execute(
                update(ReviewRevision)
                .where(ReviewRevision.review_id.in_(review_ids), ReviewRevision.text.is_not(None))
                .values(text=None)
            )
        )
        reviews = self._rowcount(
            await self._session.execute(
                update(Review)
                .where(
                    Review.business_id == self.business_id,
                    Review.client_id == user_id,
                )
                .values(
                    text=None,
                    publication_consent=False,
                    published_at=None,
                    deleted_at=changed_at,
                    deletion_reason=None,
                )
            )
        )
        references = self._rowcount(
            await self._session.execute(
                update(AppointmentReferenceMedia)
                .where(
                    AppointmentReferenceMedia.business_id == self.business_id,
                    AppointmentReferenceMedia.appointment_id.in_(appointment_ids),
                    (AppointmentReferenceMedia.telegram_file_id.is_not(None))
                    | (AppointmentReferenceMedia.telegram_file_unique_id.is_not(None)),
                )
                .values(
                    telegram_file_id=None,
                    telegram_file_unique_id=None,
                    deleted_at=func.coalesce(
                        AppointmentReferenceMedia.deleted_at,
                        changed_at,
                    ),
                    last_deletion_error="privacy_subject_anonymized",
                )
            )
        )
        payment_receipts = self._rowcount(
            await self._session.execute(
                update(Payment)
                .where(
                    Payment.business_id == self.business_id,
                    Payment.appointment_id.in_(appointment_ids),
                    (Payment.receipt_file_id.is_not(None))
                    | (Payment.receipt_file_unique_id.is_not(None))
                    | (Payment.rejection_reason.is_not(None)),
                )
                .values(
                    receipt_file_id=None,
                    receipt_file_unique_id=None,
                    receipt_media_type=None,
                    receipt_file_size=None,
                    receipt_received_at=None,
                    receipt_expires_at=None,
                    rejection_reason=None,
                )
            )
        )
        reminder_deliveries = self._rowcount(
            await self._session.execute(
                update(NotificationJob)
                .where(
                    NotificationJob.business_id == self.business_id,
                    NotificationJob.recipient_user_id == user_id,
                    NotificationJob.status.in_(
                        (NotificationJobStatus.PENDING, NotificationJobStatus.PROCESSING)
                    ),
                )
                .values(
                    status=NotificationJobStatus.CANCELLED,
                    locked_at=None,
                    locked_by=None,
                    last_error="privacy_subject_anonymized",
                )
            )
        )
        broadcast_deliveries = self._rowcount(
            await self._session.execute(
                update(BroadcastRecipient)
                .where(
                    BroadcastRecipient.business_id == self.business_id,
                    BroadcastRecipient.user_id == user_id,
                    BroadcastRecipient.status.in_(
                        (
                            BroadcastRecipientStatus.PENDING,
                            BroadcastRecipientStatus.PROCESSING,
                            BroadcastRecipientStatus.RETRY,
                        )
                    ),
                )
                .values(
                    status=BroadcastRecipientStatus.UNSUBSCRIBED,
                    locked_at=None,
                    locked_by=None,
                    last_error="privacy_subject_anonymized",
                )
            )
        )
        waitlist_deliveries = self._rowcount(
            await self._session.execute(
                update(WaitlistNotification)
                .where(
                    WaitlistNotification.business_id == self.business_id,
                    WaitlistNotification.waitlist_entry_id.in_(waitlist_ids),
                    WaitlistNotification.status.in_(
                        (
                            WaitlistNotificationStatus.PENDING,
                            WaitlistNotificationStatus.PROCESSING,
                            WaitlistNotificationStatus.RETRY,
                        )
                    ),
                )
                .values(
                    status=WaitlistNotificationStatus.CANCELLED,
                    locked_at=None,
                    locked_by=None,
                    last_error="privacy_subject_anonymized",
                )
            )
        )
        await self._session.execute(
            update(WaitlistEntry)
            .where(
                WaitlistEntry.business_id == self.business_id,
                WaitlistEntry.client_id == user_id,
                WaitlistEntry.status.in_((WaitlistStatus.ACTIVE, WaitlistStatus.MATCHED)),
            )
            .values(status=WaitlistStatus.CANCELLED)
        )
        await self._session.execute(
            delete(UserClientTag).where(
                UserClientTag.business_id == self.business_id,
                UserClientTag.user_id == user_id,
            )
        )
        await self._session.execute(
            delete(ClientAcquisitionAttribution).where(
                ClientAcquisitionAttribution.business_id == self.business_id,
                ClientAcquisitionAttribution.business_client_id == business_client_id,
            )
        )

        inactive_staff_ids = select(StaffMember.id).where(
            StaffMember.user_id == user_id,
            StaffMember.is_active.is_(False),
            StaffMember.is_bootstrap_owner.is_(False),
        )
        await self._session.execute(
            delete(MasterPublicLink).where(
                MasterPublicLink.business_id == self.business_id,
                MasterPublicLink.profile_id.in_(
                    select(MasterProfile.id).where(
                        MasterProfile.business_id == self.business_id,
                        MasterProfile.staff_member_id.in_(inactive_staff_ids),
                    )
                ),
            )
        )
        await self._session.execute(
            update(MasterProfile)
            .where(
                MasterProfile.business_id == self.business_id,
                MasterProfile.staff_member_id.in_(inactive_staff_ids),
            )
            .values(
                display_name="[anonymized]",
                bio=None,
                telegram_photo_file_id=None,
                telegram_photo_file_unique_id=None,
                telegram_url=None,
                is_published=False,
                updated_by_user_id=None,
            )
        )
        await self._session.execute(
            update(StaffInvitation)
            .where(
                StaffInvitation.business_id == self.business_id,
                StaffInvitation.accepted_by_user_id == user_id,
            )
            .values(display_name="[anonymized]", accepted_by_user_id=None)
        )
        await self._session.execute(
            update(StaffMember)
            .where(
                StaffMember.user_id == user_id,
                StaffMember.is_active.is_(False),
                StaffMember.is_bootstrap_owner.is_(False),
            )
            .values(
                user_id=None,
                display_name="[anonymized]",
                bio=None,
                specialization=None,
                telegram_photo_file_id=None,
                telegram_photo_file_unique_id=None,
                settings={},
                permission_grants=[],
            )
        )

        client.is_active = False
        client.anonymized_at = changed_at
        user.telegram_id = -(1 << 62) + user.id
        user.username = None
        user.first_name = None
        user.last_name = None
        user.phone = None
        user.role = UserRole.CLIENT
        user.privacy_consent_at = None
        user.marketing_consent_at = None
        user.marketing_unsubscribed_at = changed_at
        user.repeat_booking_opt_out_at = changed_at
        user.is_blocked = True
        user.is_self_booking_blocked = True
        user.self_booking_blocked_at = changed_at
        user.self_booking_blocked_by = None
        user.self_booking_block_reason = "privacy_subject_anonymized"
        await self._session.flush()

        return AnonymizationMutationCounts(
            identities_anonymized=2,
            notes_anonymized=notes,
            comments_anonymized=appointment_comments + appointment_history_comments,
            reviews_anonymized=reviews + review_revisions,
            reference_links_removed=references + payment_receipts,
            deliveries_cancelled=(reminder_deliveries + broadcast_deliveries + waitlist_deliveries),
            appointment_snapshots_retained=appointments_retained,
            financial_snapshots_retained=payments_retained + refunds_retained,
        )

    async def add_deletion_request(
        self,
        request: DataDeletionRequest,
    ) -> DataDeletionRequest:
        self._require_business(request.business_id)
        self._session.add(request)
        await self._session.flush()
        return request

    async def add_deletion_event(
        self,
        event: DataDeletionRequestEvent,
    ) -> DataDeletionRequestEvent:
        self._require_business(event.business_id)
        self._session.add(event)
        await self._session.flush()
        return event

    async def get_source_by_code(
        self,
        code: str,
        *,
        active_only: bool = True,
    ) -> AcquisitionSource | None:
        statement = select(AcquisitionSource).where(
            AcquisitionSource.business_id == self.business_id,
            AcquisitionSource.code == code,
        )
        if active_only:
            statement = statement.where(
                AcquisitionSource.is_active.is_(True),
                AcquisitionSource.archived_at.is_(None),
            )
        rows = await self._session.scalars(statement)
        return rows.first()

    async def add_source(self, source: AcquisitionSource) -> AcquisitionSource:
        self._require_business(source.business_id)
        self._session.add(source)
        await self._session.flush()
        return source

    async def get_attribution(
        self,
        business_client_id: int,
        *,
        for_update: bool = False,
    ) -> ClientAcquisitionAttribution | None:
        statement = select(ClientAcquisitionAttribution).where(
            ClientAcquisitionAttribution.business_id == self.business_id,
            ClientAcquisitionAttribution.business_client_id == business_client_id,
        )
        if for_update:
            statement = statement.with_for_update()
        rows = await self._session.scalars(statement)
        return rows.first()

    async def add_attribution(
        self,
        attribution: ClientAcquisitionAttribution,
    ) -> ClientAcquisitionAttribution:
        self._require_business(attribution.business_id)
        self._session.add(attribution)
        await self._session.flush()
        return attribution

    async def flush(self) -> None:
        await self._session.flush()

    def _require_business(self, business_id: int) -> None:
        if business_id != self.business_id:
            raise ValueError("entity belongs to another business")

    @staticmethod
    def _rowcount(result: object) -> int:
        return max(cast(CursorResult[Any], result).rowcount, 0)
