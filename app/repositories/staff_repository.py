"""Scoped persistence for staff memberships and one-time invitations."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.database.models.appointment import Appointment
from app.database.models.availability_window import AvailabilityWindow
from app.database.models.business import Business, StaffInvitation, StaffMember
from app.database.models.commerce import BookingReservation
from app.database.models.schedule import StaffScheduleException, StaffWeeklyInterval
from app.database.models.service_assignment import StaffServiceAssignment
from app.database.models.user import User
from app.domain.appointments import SCHEDULE_OCCUPYING_STATUSES
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    BusinessType,
    StaffInvitationStatus,
    StaffRole,
    UserRole,
)
from app.schemas.authorization import StaffIdentity


class StaffRepository:
    """Keep business and active-membership predicates at the query boundary."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_business_for_update(self, business_id: int) -> Business | None:
        return (
            await self._session.scalars(
                select(Business).where(Business.id == business_id).with_for_update()
            )
        ).one_or_none()

    async def has_active_owner(self, business_id: int) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        StaffMember.business_id == business_id,
                        StaffMember.role == StaffRole.OWNER,
                        StaffMember.is_active.is_(True),
                        StaffMember.archived_at.is_(None),
                        StaffMember.user_id.is_not(None),
                    )
                )
            )
        )

    async def get_bootstrap_owner(
        self,
        business_id: int,
        *,
        for_update: bool = False,
    ) -> StaffMember | None:
        statement = select(StaffMember).where(
            StaffMember.business_id == business_id,
            StaffMember.is_bootstrap_owner.is_(True),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def solo_transition_blockers(
        self,
        business_id: int,
        bootstrap_staff_id: int,
        *,
        now: datetime,
    ) -> tuple[str, ...]:
        """Return every blocker category without mutating staff-owned history."""

        other_staff = list(
            await self._session.scalars(
                select(StaffMember.display_name).where(
                    StaffMember.business_id == business_id,
                    StaffMember.id != bootstrap_staff_id,
                    StaffMember.is_active.is_(True),
                    StaffMember.archived_at.is_(None),
                )
            )
        )
        other_specialists = list(
            await self._session.scalars(
                select(StaffMember.display_name).where(
                    StaffMember.business_id == business_id,
                    StaffMember.id != bootstrap_staff_id,
                    StaffMember.is_active.is_(True),
                    StaffMember.is_bookable.is_(True),
                    StaffMember.archived_at.is_(None),
                )
            )
        )

        async def count(model: type[Any], *filters: ColumnElement[bool]) -> int:
            statement = select(func.count()).select_from(model).where(*filters)
            return int(await self._session.scalar(statement) or 0)

        assignments = await count(
            StaffServiceAssignment,
            StaffServiceAssignment.business_id == business_id,
            StaffServiceAssignment.staff_member_id != bootstrap_staff_id,
            StaffServiceAssignment.archived_at.is_(None),
        )
        weekly = await count(
            StaffWeeklyInterval,
            StaffWeeklyInterval.business_id == business_id,
            StaffWeeklyInterval.staff_member_id != bootstrap_staff_id,
            StaffWeeklyInterval.is_active.is_(True),
        )
        exceptions = await count(
            StaffScheduleException,
            StaffScheduleException.business_id == business_id,
            StaffScheduleException.staff_member_id != bootstrap_staff_id,
            StaffScheduleException.archived_at.is_(None),
        )
        future_appointments = await count(
            Appointment,
            Appointment.business_id == business_id,
            Appointment.staff_member_id != bootstrap_staff_id,
            Appointment.scheduled_start_at >= now,
            Appointment.status.in_(SCHEDULE_OCCUPYING_STATUSES),
        )
        blockers: list[str] = []
        if other_staff:
            blockers.append("активные сотрудники: " + ", ".join(sorted(other_staff)))
        if other_specialists:
            blockers.append("активные специалисты: " + ", ".join(sorted(other_specialists)))
        if assignments:
            blockers.append(f"назначения услуг другим специалистам: {assignments}")
        if weekly or exceptions:
            blockers.append(f"элементы расписания других специалистов: {weekly + exceptions}")
        if future_appointments:
            blockers.append(f"будущие записи других специалистов: {future_appointments}")
        return tuple(blockers)

    async def reassign_future_appointments(
        self,
        business_id: int,
        source_staff_id: int,
        target: StaffMember,
        *,
        now: datetime,
    ) -> int:
        """Move bookings only onto exact, already-open target windows under row locks."""

        appointments = list(
            await self._session.scalars(
                select(Appointment)
                .where(
                    Appointment.business_id == business_id,
                    Appointment.staff_member_id == source_staff_id,
                    Appointment.scheduled_start_at >= now,
                    Appointment.status.in_(SCHEDULE_OCCUPYING_STATUSES),
                )
                .order_by(Appointment.scheduled_start_at, Appointment.id)
                .with_for_update()
            )
        )
        if not appointments:
            return 0
        service_ids = {row.service_id for row in appointments}
        assigned_services = set(
            await self._session.scalars(
                select(StaffServiceAssignment.service_id).where(
                    StaffServiceAssignment.business_id == business_id,
                    StaffServiceAssignment.staff_member_id == target.id,
                    StaffServiceAssignment.service_id.in_(service_ids),
                    StaffServiceAssignment.is_active.is_(True),
                    StaffServiceAssignment.archived_at.is_(None),
                )
            )
        )
        if assigned_services != service_ids:
            raise ValueError("target_missing_service_assignments")

        source_windows = {
            row.id: row
            for row in await self._session.scalars(
                select(AvailabilityWindow)
                .where(
                    AvailabilityWindow.business_id == business_id,
                    AvailabilityWindow.id.in_({row.window_id for row in appointments}),
                )
                .with_for_update()
            )
        }
        target_windows = list(
            await self._session.scalars(
                select(AvailabilityWindow)
                .where(
                    AvailabilityWindow.business_id == business_id,
                    AvailabilityWindow.staff_member_id == target.id,
                    AvailabilityWindow.status == AvailabilityWindowStatus.OPEN,
                    AvailabilityWindow.start_at.in_(
                        {row.scheduled_start_at for row in appointments}
                    ),
                )
                .with_for_update()
            )
        )
        available = {(row.start_at, row.end_at): row for row in target_windows}
        if any(
            (row.scheduled_start_at, row.scheduled_end_at) not in available for row in appointments
        ):
            raise ValueError("target_missing_exact_windows")

        reservation_rows = list(
            await self._session.scalars(
                select(BookingReservation)
                .where(
                    BookingReservation.business_id == business_id,
                    BookingReservation.appointment_id.in_({row.id for row in appointments}),
                )
                .with_for_update()
            )
        )
        reservations = {row.appointment_id: row for row in reservation_rows}
        for appointment in appointments:
            old_window = source_windows.get(appointment.window_id)
            new_window = available[
                (
                    appointment.scheduled_start_at,
                    appointment.scheduled_end_at,
                )
            ]
            if old_window is None:
                raise ValueError("source_window_missing")
            old_window.status = AvailabilityWindowStatus.OPEN
            new_window.status = (
                AvailabilityWindowStatus.RESERVED
                if appointment.status
                in {
                    AppointmentStatus.PENDING_PAYMENT,
                    AppointmentStatus.PENDING_MANUAL_CONFIRMATION,
                }
                else AvailabilityWindowStatus.BOOKED
            )
            appointment.staff_member_id = target.id
            appointment.window_id = new_window.id
            appointment.master_name_snapshot = target.display_name
            reservation = reservations.get(appointment.id)
            if reservation is not None:
                reservation.staff_member_id = target.id
                reservation.window_id = new_window.id
        await self._session.flush()
        return len(appointments)

    async def get_by_id(
        self,
        business_id: int,
        staff_member_id: int,
        *,
        for_update: bool = False,
    ) -> StaffMember | None:
        statement = select(StaffMember).where(
            StaffMember.id == staff_member_id,
            StaffMember.business_id == business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_user_id(
        self,
        business_id: int,
        user_id: int,
        *,
        for_update: bool = False,
    ) -> StaffMember | None:
        statement = select(StaffMember).where(
            StaffMember.business_id == business_id,
            StaffMember.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_by_telegram_id(
        self,
        business_id: int,
        telegram_id: int,
        *,
        active_only: bool = True,
        for_update: bool = False,
    ) -> tuple[StaffMember, User] | None:
        statement = (
            select(StaffMember, User)
            .join(User, User.id == StaffMember.user_id)
            .where(
                StaffMember.business_id == business_id,
                User.telegram_id == telegram_id,
            )
        )
        if active_only:
            statement = statement.where(
                StaffMember.is_active.is_(True),
                StaffMember.archived_at.is_(None),
            )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    async def list_active_by_roles(
        self,
        business_id: int,
        roles: Collection[StaffRole],
    ) -> list[tuple[StaffMember, User]]:
        """Return bound, active staff identities scoped to one business."""

        if not roles:
            return []
        statement = (
            select(StaffMember, User)
            .join(User, User.id == StaffMember.user_id)
            .where(
                StaffMember.business_id == business_id,
                StaffMember.role.in_(roles),
                StaffMember.is_active.is_(True),
                StaffMember.archived_at.is_(None),
            )
            .order_by(StaffMember.sort_order, StaffMember.id)
        )
        rows = (await self._session.execute(statement)).all()
        return [(row[0], row[1]) for row in rows]

    async def list_members(self, business_id: int) -> list[StaffMember]:
        statement = (
            select(StaffMember)
            .where(StaffMember.business_id == business_id)
            .order_by(
                StaffMember.is_active.desc(),
                StaffMember.archived_at.asc().nulls_first(),
                StaffMember.sort_order,
                StaffMember.id,
            )
        )
        return list(await self._session.scalars(statement))

    async def has_bookable_member(self, business_id: int) -> bool:
        statement = select(StaffMember.id).where(
            StaffMember.business_id == business_id,
            StaffMember.is_active.is_(True),
            StaffMember.is_bookable.is_(True),
            StaffMember.archived_at.is_(None),
        )
        return (await self._session.scalar(statement.limit(1))) is not None

    async def sync_business_type(self, business_id: int) -> BusinessType:
        """Derive solo/salon mode from the number of active bookable specialists."""

        count = int(
            await self._session.scalar(
                select(func.count())
                .select_from(StaffMember)
                .where(
                    StaffMember.business_id == business_id,
                    StaffMember.is_active.is_(True),
                    StaffMember.is_bookable.is_(True),
                    StaffMember.archived_at.is_(None),
                )
            )
            or 0
        )
        business = await self.get_business_for_update(business_id)
        if business is None:
            raise ValueError("business_not_found")
        business.business_type = BusinessType.SALON if count > 1 else BusinessType.SOLO
        await self._session.flush()
        return business.business_type

    async def list_active_invitations(
        self,
        business_id: int,
        *,
        now: datetime,
    ) -> list[StaffInvitation]:
        statement = (
            select(StaffInvitation)
            .where(
                StaffInvitation.business_id == business_id,
                StaffInvitation.status == StaffInvitationStatus.ACTIVE,
                StaffInvitation.expires_at > now,
            )
            .order_by(StaffInvitation.expires_at, StaffInvitation.id)
        )
        return list(await self._session.scalars(statement))

    async def add(self, member: StaffMember) -> StaffMember:
        self._session.add(member)
        await self._session.flush()
        return member

    async def get_or_create_user(self, identity: StaffIdentity) -> User:
        """Bind a numeric Telegram identity without granting authority in ``users.role``."""

        insert_statement = insert(User).values(
            telegram_id=identity.telegram_id,
            username=identity.username,
            first_name=identity.first_name,
            last_name=identity.last_name,
            role=UserRole.ADMIN,
        )
        updates: dict[str, object] = {"telegram_id": identity.telegram_id}
        if identity.username is not None:
            updates["username"] = identity.username
        if identity.first_name is not None:
            updates["first_name"] = identity.first_name
        if identity.last_name is not None:
            updates["last_name"] = identity.last_name
        statement = insert_statement.on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_=updates,
        ).returning(User)
        return (await self._session.scalars(statement)).one()

    async def add_invitation(self, invitation: StaffInvitation) -> StaffInvitation:
        self._session.add(invitation)
        await self._session.flush()
        return invitation

    async def get_invitation_by_digest(
        self,
        token_digest: str,
        *,
        for_update: bool = False,
    ) -> StaffInvitation | None:
        statement = select(StaffInvitation).where(StaffInvitation.token_digest == token_digest)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_invitation_by_id(
        self,
        business_id: int,
        invitation_id: int,
        *,
        for_update: bool = False,
    ) -> StaffInvitation | None:
        statement = select(StaffInvitation).where(
            StaffInvitation.id == invitation_id,
            StaffInvitation.business_id == business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def flush(self) -> None:
        await self._session.flush()
