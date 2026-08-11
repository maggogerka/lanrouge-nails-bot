"""Administrative service catalog use cases."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.database.models import Service, StaffServiceAssignment
from app.domain.errors import EntityNotFoundError, ServiceInUseError
from app.domain.tenancy import DEFAULT_STAFF_MEMBER_ID
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.service import AdminActor, ServiceCreate, ServicePatch, ServiceView
from app.services.appointment_common import ensure_admin

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class ServiceCatalog:
    """Validate authorization and coordinate catalog transactions."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def list_services(self, actor: AdminActor) -> list[ServiceView]:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            services = await unit_of_work.services.list_all()
            return [ServiceView.model_validate(service) for service in services]

    async def get_service(self, actor: AdminActor, service_id: int) -> ServiceView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            service = await self._get_required(unit_of_work, service_id)
            return ServiceView.model_validate(service)

    async def create_service(
        self,
        actor: AdminActor,
        values: ServiceCreate,
        *,
        correlation_id: str | None = None,
    ) -> ServiceView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            service = await unit_of_work.services.add(
                Service(
                    business_id=unit_of_work.business_id,
                    name=values.name,
                    description=values.description,
                    price=values.price,
                    duration_min_minutes=values.duration_min_minutes,
                    duration_max_minutes=values.duration_max_minutes,
                    prepayment_amount=values.prepayment_amount,
                    is_active=True,
                )
            )
            await unit_of_work.service_assignments.add_assignment(
                StaffServiceAssignment(
                    business_id=unit_of_work.business_id,
                    staff_member_id=DEFAULT_STAFF_MEMBER_ID,
                    service_id=service.id,
                    online_booking_enabled=True,
                    is_active=True,
                )
            )
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="service.created",
                entity_type="service",
                entity_id=str(service.id),
                changes={"after": self._audit_values(service)},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return ServiceView.model_validate(service)

    async def update_service(
        self,
        actor: AdminActor,
        service_id: int,
        patch: ServicePatch,
        *,
        correlation_id: str | None = None,
    ) -> ServiceView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            service = await self._get_required(unit_of_work, service_id, for_update=True)
            before = self._audit_values(service)

            merged = ServiceCreate(
                name=(
                    patch.name
                    if "name" in patch.model_fields_set and patch.name is not None
                    else service.name
                ),
                description=(
                    patch.description
                    if "description" in patch.model_fields_set
                    else service.description
                ),
                price=(
                    patch.price
                    if "price" in patch.model_fields_set and patch.price is not None
                    else service.price
                ),
                duration_min_minutes=(
                    patch.duration_min_minutes
                    if "duration_min_minutes" in patch.model_fields_set
                    and patch.duration_min_minutes is not None
                    else service.duration_min_minutes
                ),
                duration_max_minutes=(
                    patch.duration_max_minutes
                    if "duration_max_minutes" in patch.model_fields_set
                    and patch.duration_max_minutes is not None
                    else service.duration_max_minutes
                ),
                prepayment_amount=(
                    patch.prepayment_amount
                    if "prepayment_amount" in patch.model_fields_set
                    and patch.prepayment_amount is not None
                    else service.prepayment_amount
                ),
            )
            service.name = merged.name
            service.description = merged.description
            service.price = merged.price
            service.duration_min_minutes = merged.duration_min_minutes
            service.duration_max_minutes = merged.duration_max_minutes
            service.prepayment_amount = merged.prepayment_amount
            await unit_of_work.session.flush()

            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="service.updated",
                entity_type="service",
                entity_id=str(service.id),
                changes={"before": before, "after": self._audit_values(service)},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return ServiceView.model_validate(service)

    async def set_active(
        self,
        actor: AdminActor,
        service_id: int,
        *,
        is_active: bool,
        correlation_id: str | None = None,
    ) -> ServiceView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            service = await self._get_required(unit_of_work, service_id, for_update=True)
            before = service.is_active
            service.is_active = is_active
            await unit_of_work.session.flush()
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="service.activated" if is_active else "service.archived",
                entity_type="service",
                entity_id=str(service.id),
                changes={"is_active": {"before": before, "after": is_active}},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return ServiceView.model_validate(service)

    async def delete_unused_service(
        self,
        actor: AdminActor,
        service_id: int,
        *,
        correlation_id: str | None = None,
    ) -> None:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            service = await self._get_required(unit_of_work, service_id, for_update=True)
            if await unit_of_work.services.has_appointments(service_id):
                raise ServiceInUseError(
                    "Услуга уже использовалась в записях и может быть только архивирована."
                )
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="service.deleted",
                entity_type="service",
                entity_id=str(service.id),
                changes={"before": self._audit_values(service)},
                correlation_id=correlation_id,
            )
            await unit_of_work.services.delete(service)
            await unit_of_work.commit()

    def _ensure_admin(self, actor: AdminActor) -> None:
        ensure_admin(actor, self._admin_telegram_ids)

    @staticmethod
    async def _get_required(
        unit_of_work: SqlAlchemyUnitOfWork,
        service_id: int,
        *,
        for_update: bool = False,
    ) -> Service:
        service = await unit_of_work.services.get(service_id, for_update=for_update)
        if service is None:
            raise EntityNotFoundError("Услуга не найдена.")
        return service

    @staticmethod
    def _audit_values(service: Service) -> dict[str, str | int | bool | None]:
        return {
            "name": service.name,
            "description": service.description,
            "price": str(Decimal(service.price)),
            "duration_min_minutes": service.duration_min_minutes,
            "duration_max_minutes": service.duration_max_minutes,
            "prepayment_amount": str(Decimal(service.prepayment_amount)),
            "is_active": service.is_active,
        }
