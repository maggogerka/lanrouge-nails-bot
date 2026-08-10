"""Business-scoped catalog categories and per-staff service assignments."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.business import StaffMember
from app.database.models.service import Service
from app.database.models.service_assignment import (
    ServiceCategory,
    StaffServiceAssignment,
)


class ServiceAssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_categories(self, business_id: int) -> list[ServiceCategory]:
        rows = await self._session.scalars(
            select(ServiceCategory)
            .where(
                ServiceCategory.business_id == business_id,
                ServiceCategory.archived_at.is_(None),
            )
            .order_by(ServiceCategory.sort_order, ServiceCategory.name, ServiceCategory.id)
        )
        return list(rows.all())

    async def get_assignment(
        self,
        business_id: int,
        staff_member_id: int,
        service_id: int,
        *,
        for_update: bool = False,
    ) -> StaffServiceAssignment | None:
        statement = select(StaffServiceAssignment).where(
            StaffServiceAssignment.business_id == business_id,
            StaffServiceAssignment.staff_member_id == staff_member_id,
            StaffServiceAssignment.service_id == service_id,
            StaffServiceAssignment.archived_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def list_bookable_assignments(
        self, business_id: int, service_id: int
    ) -> list[tuple[StaffServiceAssignment, Service, StaffMember]]:
        rows = await self._session.execute(
            select(StaffServiceAssignment, Service, StaffMember)
            .join(Service, Service.id == StaffServiceAssignment.service_id)
            .join(StaffMember, StaffMember.id == StaffServiceAssignment.staff_member_id)
            .where(
                StaffServiceAssignment.business_id == business_id,
                StaffServiceAssignment.service_id == service_id,
                StaffServiceAssignment.is_active.is_(True),
                StaffServiceAssignment.online_booking_enabled.is_(True),
                StaffServiceAssignment.archived_at.is_(None),
                Service.business_id == business_id,
                Service.is_active.is_(True),
                Service.online_booking_enabled.is_(True),
                Service.archived_at.is_(None),
                StaffMember.business_id == business_id,
                StaffMember.is_active.is_(True),
                StaffMember.is_bookable.is_(True),
                StaffMember.archived_at.is_(None),
            )
            .order_by(
                StaffMember.sort_order,
                StaffServiceAssignment.sort_order,
                StaffMember.id,
            )
        )
        return [(row[0], row[1], row[2]) for row in rows.all()]

    async def add_category(self, category: ServiceCategory) -> ServiceCategory:
        self._session.add(category)
        await self._session.flush()
        return category

    async def add_assignment(self, assignment: StaffServiceAssignment) -> StaffServiceAssignment:
        self._session.add(assignment)
        await self._session.flush()
        return assignment
