"""Business-scoped reservation persistence and expiry claiming."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatusHistory
from app.database.models.availability_window import AvailabilityWindow
from app.database.models.business import BusinessClient
from app.database.models.commerce import BookingReservation, BusinessPaymentSettings
from app.domain.appointments import SCHEDULE_OCCUPYING_STATUSES
from app.domain.enums import AppointmentStatus, ReservationStatus
from app.repositories.scoped import TenantScopedRepository


class ReservationRepository(TenantScopedRepository):
    """All reservation, appointment and window reads are tenant-scoped."""

    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def add(self, reservation: BookingReservation) -> BookingReservation:
        self._require_business(reservation.business_id)
        self._session.add(reservation)
        await self._session.flush()
        return reservation

    async def get(
        self, reservation_id: int, *, for_update: bool = False
    ) -> BookingReservation | None:
        statement = select(BookingReservation).where(
            BookingReservation.id == reservation_id,
            BookingReservation.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_idempotency_key(
        self, idempotency_key: str, *, for_update: bool = False
    ) -> BookingReservation | None:
        statement = select(BookingReservation).where(
            BookingReservation.business_id == self.business_id,
            BookingReservation.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_token_digest(
        self, token_digest: str, *, for_update: bool = False
    ) -> BookingReservation | None:
        statement = select(BookingReservation).where(
            BookingReservation.business_id == self.business_id,
            BookingReservation.token_digest == token_digest,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_active_for_window(
        self, window_id: int, *, for_update: bool = False
    ) -> BookingReservation | None:
        statement = select(BookingReservation).where(
            BookingReservation.business_id == self.business_id,
            BookingReservation.window_id == window_id,
            BookingReservation.status.in_(
                {ReservationStatus.ACTIVE, ReservationStatus.AWAITING_REVIEW}
            ),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_active_for_appointment(
        self, appointment_id: int, *, for_update: bool = False
    ) -> BookingReservation | None:
        statement = select(BookingReservation).where(
            BookingReservation.business_id == self.business_id,
            BookingReservation.appointment_id == appointment_id,
            BookingReservation.status.in_(
                {ReservationStatus.ACTIVE, ReservationStatus.AWAITING_REVIEW}
            ),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def claim_expired(self, *, now: datetime, limit: int) -> list[BookingReservation]:
        """Lock expired active rows so multiple workers can partition work safely."""

        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = await self._session.scalars(
            select(BookingReservation)
            .where(
                BookingReservation.business_id == self.business_id,
                BookingReservation.status == ReservationStatus.ACTIVE,
                BookingReservation.expires_at <= now,
            )
            .order_by(BookingReservation.expires_at, BookingReservation.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows.all())

    async def get_window_for_update(self, window_id: int) -> AvailabilityWindow | None:
        return (
            await self._session.scalars(
                select(AvailabilityWindow)
                .where(
                    AvailabilityWindow.id == window_id,
                    AvailabilityWindow.business_id == self.business_id,
                )
                .with_for_update()
            )
        ).one_or_none()

    async def get_appointment_for_update(self, appointment_id: int) -> Appointment | None:
        return (
            await self._session.scalars(
                select(Appointment)
                .where(
                    Appointment.id == appointment_id,
                    Appointment.business_id == self.business_id,
                )
                .with_for_update()
            )
        ).one_or_none()

    async def add_history(self, history: AppointmentStatusHistory) -> None:
        self._session.add(history)
        await self._session.flush()

    async def payment_settings(
        self,
        *,
        for_update: bool = False,
    ) -> BusinessPaymentSettings | None:
        return await self._session.get(
            BusinessPaymentSettings,
            self.business_id,
            with_for_update=for_update,
        )

    async def lock_client_for_booking(self, client_id: int) -> BusinessClient | None:
        """Serialize per-client quota checks inside the caller's booking transaction."""

        return (
            await self._session.scalars(
                select(BusinessClient)
                .where(
                    BusinessClient.business_id == self.business_id,
                    BusinessClient.user_id == client_id,
                    BusinessClient.is_active.is_(True),
                    BusinessClient.anonymized_at.is_(None),
                )
                .with_for_update()
            )
        ).one_or_none()

    async def count_future_appointments(self, *, client_id: int, now: datetime) -> int:
        return int(
            await self._session.scalar(
                select(func.count(Appointment.id)).where(
                    Appointment.business_id == self.business_id,
                    Appointment.client_id == client_id,
                    Appointment.status.in_(SCHEDULE_OCCUPYING_STATUSES),
                    Appointment.scheduled_start_at > now,
                )
            )
            or 0
        )

    async def count_client_appointments_between(
        self,
        *,
        client_id: int,
        start_at: datetime,
        end_at: datetime,
    ) -> int:
        return int(
            await self._session.scalar(
                select(func.count(Appointment.id)).where(
                    Appointment.business_id == self.business_id,
                    Appointment.client_id == client_id,
                    Appointment.status.in_(SCHEDULE_OCCUPYING_STATUSES),
                    Appointment.scheduled_start_at >= start_at,
                    Appointment.scheduled_start_at < end_at,
                )
            )
            or 0
        )

    async def count_active_reservations(self, *, client_id: int, now: datetime) -> int:
        return int(
            await self._session.scalar(
                select(func.count(BookingReservation.id)).where(
                    BookingReservation.business_id == self.business_id,
                    BookingReservation.client_id == client_id,
                    (
                        (BookingReservation.status == ReservationStatus.ACTIVE)
                        & (BookingReservation.expires_at > now)
                    )
                    | (BookingReservation.status == ReservationStatus.AWAITING_REVIEW),
                )
            )
            or 0
        )

    @staticmethod
    def appointment_can_expire(appointment: Appointment) -> bool:
        return appointment.status in {
            AppointmentStatus.PENDING_PAYMENT,
            AppointmentStatus.PENDING_MANUAL_CONFIRMATION,
        }

    def _require_business(self, business_id: int) -> None:
        if business_id != self.business_id:
            raise ValueError("entity belongs to another business")
