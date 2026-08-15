"""Explicit owner-only destructive cleanup for catalog and schedule records."""

from __future__ import annotations

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Appointment,
    AppointmentAddonSnapshot,
    AppointmentReferenceMedia,
    AppointmentStatusHistory,
    AvailabilityWindow,
    BookingReservation,
    NotificationJob,
    Payment,
    PaymentWebhookEvent,
    PortfolioItem,
    Refund,
    Review,
    ReviewRevision,
    Service,
    ServiceAddon,
    StaffServiceAssignment,
    WaitlistEntry,
    WaitlistNotification,
    WorkstationService,
)
from app.domain.enums import AvailabilityWindowStatus
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository


class HardDeleteRepository(TenantScopedRepository):
    """Delete a bounded aggregate only after an explicit owner confirmation."""

    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def delete_window_with_history(self, window_id: int) -> int:
        appointment_ids = await self._appointment_ids(window_id=window_id)

        await self._session.execute(
            delete(WaitlistNotification).where(
                WaitlistNotification.business_id == self.business_id,
                WaitlistNotification.window_id == window_id,
            )
        )
        await self._session.execute(
            delete(BookingReservation).where(
                BookingReservation.business_id == self.business_id,
                BookingReservation.window_id == window_id,
            )
        )
        await self._delete_appointments(appointment_ids)
        await self._session.execute(
            delete(AvailabilityWindow).where(
                AvailabilityWindow.business_id == self.business_id,
                AvailabilityWindow.id == window_id,
            )
        )
        await self._session.flush()
        return len(appointment_ids)

    async def delete_service_with_history(self, service_id: int) -> int:
        appointment_rows = await self._session.execute(
            select(Appointment.id, Appointment.window_id).where(
                Appointment.business_id == self.business_id,
                Appointment.service_id == service_id,
            )
        )
        rows = list(appointment_rows.all())
        appointment_ids = [int(row.id) for row in rows]
        appointment_window_ids = sorted(
            {int(row.window_id) for row in rows if row.window_id is not None}
        )

        waitlist_condition = WaitlistEntry.service_id == service_id
        if appointment_ids:
            waitlist_condition = or_(
                waitlist_condition,
                WaitlistEntry.booked_appointment_id.in_(appointment_ids),
            )
        waitlist_ids = list(
            (
                await self._session.scalars(
                    select(WaitlistEntry.id).where(
                        WaitlistEntry.business_id == self.business_id,
                        waitlist_condition,
                    )
                )
            ).all()
        )
        await self._delete_waitlist_entries(waitlist_ids)

        await self._session.execute(
            delete(BookingReservation).where(
                BookingReservation.business_id == self.business_id,
                BookingReservation.service_id == service_id,
            )
        )

        addon_ids = list(
            (
                await self._session.scalars(
                    select(ServiceAddon.id).where(
                        ServiceAddon.business_id == self.business_id,
                        ServiceAddon.service_id == service_id,
                    )
                )
            ).all()
        )
        if addon_ids:
            await self._session.execute(
                delete(AppointmentAddonSnapshot).where(
                    AppointmentAddonSnapshot.business_id == self.business_id,
                    AppointmentAddonSnapshot.service_addon_id.in_(addon_ids),
                )
            )

        await self._delete_appointments(appointment_ids)

        window_filter = AvailabilityWindow.service_id == service_id
        if appointment_window_ids:
            window_filter = or_(
                window_filter,
                AvailabilityWindow.id.in_(appointment_window_ids),
            )
        await self._session.execute(
            update(AvailabilityWindow)
            .where(
                AvailabilityWindow.business_id == self.business_id,
                window_filter,
            )
            .values(
                status=AvailabilityWindowStatus.CLOSED,
                service_id=None,
                workstation_id=None,
            )
        )

        await self._session.execute(
            delete(StaffServiceAssignment).where(
                StaffServiceAssignment.business_id == self.business_id,
                StaffServiceAssignment.service_id == service_id,
            )
        )
        await self._session.execute(
            delete(WorkstationService).where(
                WorkstationService.business_id == self.business_id,
                WorkstationService.service_id == service_id,
            )
        )
        await self._session.execute(
            update(PortfolioItem)
            .where(
                PortfolioItem.business_id == self.business_id,
                PortfolioItem.linked_service_id == service_id,
            )
            .values(linked_service_id=None)
        )
        await self._session.execute(
            delete(ServiceAddon).where(
                ServiceAddon.business_id == self.business_id,
                ServiceAddon.service_id == service_id,
            )
        )
        await self._session.execute(
            delete(Service).where(
                Service.business_id == self.business_id,
                Service.id == service_id,
            )
        )
        await self._session.flush()
        return len(appointment_ids)

    async def _appointment_ids(self, *, window_id: int) -> list[int]:
        rows = await self._session.scalars(
            select(Appointment.id).where(
                Appointment.business_id == self.business_id,
                Appointment.window_id == window_id,
            )
        )
        return [int(value) for value in rows.all()]

    async def _delete_appointments(self, appointment_ids: list[int]) -> None:
        if not appointment_ids:
            return

        payment_ids = list(
            (
                await self._session.scalars(
                    select(Payment.id).where(
                        Payment.business_id == self.business_id,
                        Payment.appointment_id.in_(appointment_ids),
                    )
                )
            ).all()
        )
        if payment_ids:
            await self._session.execute(
                delete(Refund).where(
                    Refund.business_id == self.business_id,
                    Refund.payment_id.in_(payment_ids),
                )
            )
            await self._session.execute(
                delete(PaymentWebhookEvent).where(
                    PaymentWebhookEvent.business_id == self.business_id,
                    PaymentWebhookEvent.payment_id.in_(payment_ids),
                )
            )
            await self._session.execute(
                delete(Payment).where(
                    Payment.business_id == self.business_id,
                    Payment.id.in_(payment_ids),
                )
            )

        review_ids = list(
            (
                await self._session.scalars(
                    select(Review.id).where(
                        Review.business_id == self.business_id,
                        Review.appointment_id.in_(appointment_ids),
                    )
                )
            ).all()
        )
        if review_ids:
            await self._session.execute(
                delete(ReviewRevision).where(ReviewRevision.review_id.in_(review_ids))
            )
            await self._session.execute(
                delete(Review).where(
                    Review.business_id == self.business_id,
                    Review.id.in_(review_ids),
                )
            )

        booked_waitlist_ids = list(
            (
                await self._session.scalars(
                    select(WaitlistEntry.id).where(
                        WaitlistEntry.business_id == self.business_id,
                        WaitlistEntry.booked_appointment_id.in_(appointment_ids),
                    )
                )
            ).all()
        )
        await self._delete_waitlist_entries(booked_waitlist_ids)

        scoped_models = (
            NotificationJob,
            AppointmentReferenceMedia,
            AppointmentAddonSnapshot,
            BookingReservation,
        )
        for model in scoped_models:
            await self._session.execute(
                delete(model).where(
                    model.business_id == self.business_id,
                    model.appointment_id.in_(appointment_ids),
                )
            )
        await self._session.execute(
            delete(AppointmentStatusHistory).where(
                AppointmentStatusHistory.appointment_id.in_(appointment_ids)
            )
        )
        await self._session.execute(
            update(Appointment)
            .where(
                Appointment.business_id == self.business_id,
                Appointment.rescheduled_from_id.in_(appointment_ids),
            )
            .values(rescheduled_from_id=None)
        )
        await self._session.execute(
            delete(Appointment).where(
                Appointment.business_id == self.business_id,
                Appointment.id.in_(appointment_ids),
            )
        )

    async def _delete_waitlist_entries(self, entry_ids: list[int]) -> None:
        if not entry_ids:
            return
        await self._session.execute(
            delete(WaitlistNotification).where(
                WaitlistNotification.business_id == self.business_id,
                WaitlistNotification.waitlist_entry_id.in_(entry_ids),
            )
        )
        await self._session.execute(
            delete(WaitlistEntry).where(
                WaitlistEntry.business_id == self.business_id,
                WaitlistEntry.id.in_(entry_ids),
            )
        )
