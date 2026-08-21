"""Transactional, Telegram-user-scoped public demo application service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.demo import (
    DemoAppointment,
    DemoClient,
    DemoSession,
    DemoSlot,
    DemoStaff,
)
from app.database.models.demo import DemoService as DemoServiceModel
from app.demo.policy import DemoOperation, DemoPolicy
from app.demo.seed import build_slot_seed

MAX_ACTIVE_APPOINTMENTS: Final = 5
MAX_CLIENTS: Final = 10
MAX_SERVICES: Final = 8
ACTIVE_STATUSES: Final = frozenset({"confirmed", "client_confirmed"})


class DemoError(RuntimeError):
    pass


class DemoStaleAction(DemoError):
    pass


class DemoLimitReached(DemoError):
    pass


class DemoResetCooldown(DemoError):
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        super().__init__(f"Повторный сброс будет доступен через {seconds} сек.")


@dataclass(frozen=True, slots=True)
class DemoWorkspace:
    generation: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DemoAppointmentView:
    id: int
    client_name: str
    service_name: str
    staff_name: str
    start_at: datetime
    status: str


@dataclass(frozen=True, slots=True)
class DemoSlotView:
    id: int
    service_id: int
    service_name: str
    staff_name: str
    start_at: datetime


class DemoService:
    """Own all demo reads and writes so scoping cannot be omitted by handlers."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        timezone: ZoneInfo,
        session_ttl_hours: int = 2,
        retention_hours: int = 24,
        reset_cooldown_seconds: int = 60,
        policy: DemoPolicy | None = None,
    ) -> None:
        self._sessions = sessions
        self._timezone = timezone
        self._session_ttl = timedelta(hours=session_ttl_hours)
        self._retention = timedelta(hours=retention_hours)
        self._reset_cooldown = timedelta(seconds=reset_cooldown_seconds)
        self.policy = policy or DemoPolicy()

    async def ensure_workspace(
        self, telegram_user_id: int, *, now: datetime | None = None
    ) -> DemoWorkspace:
        self.policy.require(DemoOperation.READ)
        current = self._now(now)
        async with self._sessions() as database:
            async with database.begin():
                await database.execute(
                    insert(DemoSession)
                    .values(
                        telegram_user_id=telegram_user_id,
                        generation=1,
                        expires_at=current + self._session_ttl,
                    )
                    .on_conflict_do_nothing(index_elements=[DemoSession.telegram_user_id])
                )
                workspace = await self._locked_workspace(database, telegram_user_id)
                if workspace.expires_at <= current:
                    await self._reset_locked(database, workspace, current)
                if not await self._has_seed(database, workspace.id):
                    await self._seed(database, workspace, current)
                return DemoWorkspace(workspace.generation, workspace.expires_at)

    async def list_services(self, telegram_user_id: int) -> tuple[DemoServiceModel, ...]:
        async with self._sessions() as database:
            workspace = await self._workspace(database, telegram_user_id)
            rows = await database.scalars(
                select(DemoServiceModel)
                .where(DemoServiceModel.session_id == workspace.id, DemoServiceModel.is_active)
                .order_by(DemoServiceModel.id)
            )
            return tuple(rows)

    async def list_slots(
        self, telegram_user_id: int, generation: int, service_id: int
    ) -> tuple[DemoSlotView, ...]:
        async with self._sessions() as database:
            workspace = await self._workspace(database, telegram_user_id, generation)
            rows = await database.execute(
                select(DemoSlot, DemoServiceModel.name, DemoStaff.name)
                .join(DemoServiceModel, DemoServiceModel.id == DemoSlot.service_id)
                .join(DemoStaff, DemoStaff.id == DemoSlot.staff_id)
                .where(
                    DemoSlot.session_id == workspace.id,
                    DemoSlot.service_id == service_id,
                    DemoSlot.is_available,
                )
                .order_by(DemoSlot.start_at)
                .limit(12)
            )
            return tuple(
                DemoSlotView(slot.id, slot.service_id, service_name, staff_name, slot.start_at)
                for slot, service_name, staff_name in rows
            )

    async def list_appointments(
        self, telegram_user_id: int
    ) -> tuple[DemoAppointmentView, ...]:
        async with self._sessions() as database:
            workspace = await self._workspace(database, telegram_user_id)
            rows = await database.execute(
                select(
                    DemoAppointment,
                    DemoClient.display_name,
                    DemoServiceModel.name,
                    DemoStaff.name,
                )
                .join(DemoClient, DemoClient.id == DemoAppointment.client_id)
                .join(DemoServiceModel, DemoServiceModel.id == DemoAppointment.service_id)
                .join(DemoStaff, DemoStaff.id == DemoAppointment.staff_id)
                .where(DemoAppointment.session_id == workspace.id)
                .order_by(DemoAppointment.start_at)
                .limit(20)
            )
            return tuple(
                DemoAppointmentView(
                    appointment.id,
                    client_name,
                    service_name,
                    staff_name,
                    appointment.start_at,
                    appointment.status,
                )
                for appointment, client_name, service_name, staff_name in rows
            )

    async def list_clients(self, telegram_user_id: int) -> tuple[tuple[str, int], ...]:
        async with self._sessions() as database:
            workspace = await self._workspace(database, telegram_user_id)
            rows = await database.execute(
                select(DemoClient.display_name, func.count(DemoAppointment.id))
                .outerjoin(DemoAppointment, DemoAppointment.client_id == DemoClient.id)
                .where(DemoClient.session_id == workspace.id)
                .group_by(DemoClient.id)
                .order_by(DemoClient.id)
            )
            return tuple((name, int(count)) for name, count in rows)

    async def list_schedule(self, telegram_user_id: int) -> tuple[DemoSlotView, ...]:
        async with self._sessions() as database:
            workspace = await self._workspace(database, telegram_user_id)
            rows = await database.execute(
                select(DemoSlot, DemoServiceModel.name, DemoStaff.name)
                .join(DemoServiceModel, DemoServiceModel.id == DemoSlot.service_id)
                .join(DemoStaff, DemoStaff.id == DemoSlot.staff_id)
                .where(DemoSlot.session_id == workspace.id, DemoSlot.is_available)
                .order_by(DemoSlot.start_at)
                .limit(15)
            )
            return tuple(
                DemoSlotView(slot.id, slot.service_id, service_name, staff_name, slot.start_at)
                for slot, service_name, staff_name in rows
            )

    async def book(
        self, telegram_user_id: int, generation: int, slot_id: int
    ) -> DemoAppointmentView:
        self.policy.require(DemoOperation.BOOK)
        async with self._sessions() as database:
            async with database.begin():
                workspace = await self._locked_workspace(database, telegram_user_id, generation)
                active_count = await database.scalar(
                    select(func.count(DemoAppointment.id)).where(
                        DemoAppointment.session_id == workspace.id,
                        DemoAppointment.status.in_(ACTIVE_STATUSES),
                    )
                )
                if int(active_count or 0) >= MAX_ACTIVE_APPOINTMENTS:
                    raise DemoLimitReached("В демо можно иметь не более 5 активных записей.")
                slot = await database.scalar(
                    select(DemoSlot)
                    .where(DemoSlot.id == slot_id, DemoSlot.session_id == workspace.id)
                    .with_for_update()
                )
                if slot is None or not slot.is_available:
                    raise DemoError("Это окно уже занято или недоступно.")
                client = await database.scalar(
                    select(DemoClient)
                    .where(DemoClient.session_id == workspace.id)
                    .order_by(DemoClient.id)
                    .limit(1)
                )
                service = await database.get(DemoServiceModel, slot.service_id)
                staff = await database.get(DemoStaff, slot.staff_id)
                if client is None or service is None or staff is None:
                    raise DemoError("Демо-данные устарели. Выполните сброс.")
                slot.is_available = False
                appointment = DemoAppointment(
                    session_id=workspace.id,
                    client_id=client.id,
                    service_id=service.id,
                    staff_id=staff.id,
                    slot_id=slot.id,
                    start_at=slot.start_at,
                    end_at=slot.end_at,
                    status="confirmed",
                )
                database.add(appointment)
                await database.flush()
                return DemoAppointmentView(
                    appointment.id,
                    client.display_name,
                    service.name,
                    staff.name,
                    appointment.start_at,
                    appointment.status,
                )

    async def update_appointment(
        self, telegram_user_id: int, generation: int, appointment_id: int, status: str
    ) -> None:
        self.policy.require(DemoOperation.UPDATE_APPOINTMENT)
        if status not in {"client_confirmed", "cancelled_by_admin"}:
            raise DemoError("Недопустимый статус демо-записи.")
        async with self._sessions() as database:
            async with database.begin():
                workspace = await self._locked_workspace(database, telegram_user_id, generation)
                appointment = await database.scalar(
                    select(DemoAppointment)
                    .where(
                        DemoAppointment.id == appointment_id,
                        DemoAppointment.session_id == workspace.id,
                    )
                    .with_for_update()
                )
                if appointment is None:
                    raise DemoError("Запись не найдена в вашем демо.")
                appointment.status = status

    async def add_service(self, telegram_user_id: int, generation: int) -> str:
        self.policy.require(DemoOperation.ADD_SERVICE)
        async with self._sessions() as database:
            async with database.begin():
                workspace = await self._locked_workspace(database, telegram_user_id, generation)
                count = int(
                    await database.scalar(
                        select(func.count(DemoServiceModel.id)).where(
                            DemoServiceModel.session_id == workspace.id
                        )
                    )
                    or 0
                )
                if count >= MAX_SERVICES:
                    raise DemoLimitReached("В демо можно создать не более 8 услуг.")
                name = f"Дополнительная услуга {count - 2}"
                database.add(
                    DemoServiceModel(
                        session_id=workspace.id,
                        name=name,
                        duration_minutes=60,
                        price=Decimal("1500.00"),
                        is_active=True,
                    )
                )
                return name

    async def add_window(self, telegram_user_id: int, generation: int) -> datetime:
        self.policy.require(DemoOperation.ADD_WINDOW)
        async with self._sessions() as database:
            async with database.begin():
                workspace = await self._locked_workspace(database, telegram_user_id, generation)
                staff = await database.scalar(
                    select(DemoStaff)
                    .where(DemoStaff.session_id == workspace.id)
                    .order_by(DemoStaff.id)
                )
                service = await database.scalar(
                    select(DemoServiceModel)
                    .where(DemoServiceModel.session_id == workspace.id)
                    .order_by(DemoServiceModel.id)
                )
                latest = await database.scalar(
                    select(func.max(DemoSlot.start_at)).where(DemoSlot.session_id == workspace.id)
                )
                if staff is None or service is None:
                    raise DemoError("Демо-данные устарели. Выполните сброс.")
                start_at = (latest or datetime.now(UTC)) + timedelta(days=1)
                database.add(
                    DemoSlot(
                        session_id=workspace.id,
                        staff_id=staff.id,
                        service_id=service.id,
                        start_at=start_at,
                        end_at=start_at + timedelta(minutes=service.duration_minutes),
                        is_available=True,
                    )
                )
                return start_at

    async def reset(self, telegram_user_id: int, generation: int) -> DemoWorkspace:
        self.policy.require(DemoOperation.RESET)
        current = datetime.now(UTC)
        async with self._sessions() as database:
            async with database.begin():
                workspace = await self._locked_workspace(database, telegram_user_id, generation)
                if workspace.last_reset_at is not None:
                    retry_at = workspace.last_reset_at + self._reset_cooldown
                    if retry_at > current:
                        seconds = max(1, int((retry_at - current).total_seconds()))
                        raise DemoResetCooldown(seconds)
                await self._reset_locked(database, workspace, current)
                return DemoWorkspace(workspace.generation, workspace.expires_at)

    async def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current = self._now(now)
        cutoff = current - self._retention
        async with self._sessions() as database:
            async with database.begin():
                deleted_ids = await database.scalars(
                    delete(DemoSession)
                    .where(DemoSession.updated_at < cutoff)
                    .returning(DemoSession.id)
                )
                return len(tuple(deleted_ids))

    async def _reset_locked(
        self, database: AsyncSession, workspace: DemoSession, current: datetime
    ) -> None:
        for model in (DemoAppointment, DemoSlot, DemoClient, DemoStaff, DemoServiceModel):
            await database.execute(delete(model).where(model.session_id == workspace.id))
        workspace.generation += 1
        workspace.expires_at = current + self._session_ttl
        workspace.last_reset_at = current
        workspace.updated_at = current
        await database.flush()
        await self._seed(database, workspace, current)

    async def _seed(
        self, database: AsyncSession, workspace: DemoSession, current: datetime
    ) -> None:
        services = [
            DemoServiceModel(
                session_id=workspace.id,
                name="Консультация",
                duration_minutes=60,
                price=Decimal("1200"),
                is_active=True,
            ),
            DemoServiceModel(
                session_id=workspace.id,
                name="Основная услуга",
                duration_minutes=90,
                price=Decimal("2500"),
                is_active=True,
            ),
            DemoServiceModel(
                session_id=workspace.id,
                name="Экспресс-услуга",
                duration_minutes=60,
                price=Decimal("1800"),
                is_active=True,
            ),
        ]
        staff = [
            DemoStaff(session_id=workspace.id, name=f"Мастер {index}", is_active=True)
            for index in range(1, 4)
        ]
        clients = [
            DemoClient(session_id=workspace.id, display_name=f"Клиент {index}")
            for index in range(1, 5)
        ]
        database.add_all([*services, *staff, *clients])
        await database.flush()
        slots = [
            DemoSlot(
                session_id=workspace.id,
                staff_id=staff[item.staff_index].id,
                service_id=services[item.service_index].id,
                start_at=item.start_at,
                end_at=item.end_at,
                is_available=True,
            )
            for item in build_slot_seed(current, self._timezone)
        ]
        database.add_all(slots)
        await database.flush()
        for index, slot in enumerate(slots[:2]):
            slot.is_available = False
            database.add(
                DemoAppointment(
                    session_id=workspace.id,
                    client_id=clients[index].id,
                    service_id=slot.service_id,
                    staff_id=slot.staff_id,
                    slot_id=slot.id,
                    start_at=slot.start_at,
                    end_at=slot.end_at,
                    status="confirmed",
                )
            )

    async def _has_seed(self, database: AsyncSession, session_id: int) -> bool:
        return bool(
            await database.scalar(
                select(func.count(DemoServiceModel.id)).where(
                    DemoServiceModel.session_id == session_id
                )
            )
        )

    async def _workspace(
        self, database: AsyncSession, telegram_user_id: int, generation: int | None = None
    ) -> DemoSession:
        workspace = await database.scalar(
            select(DemoSession).where(DemoSession.telegram_user_id == telegram_user_id)
        )
        return self._validate_workspace(workspace, generation)

    async def _locked_workspace(
        self, database: AsyncSession, telegram_user_id: int, generation: int | None = None
    ) -> DemoSession:
        workspace = await database.scalar(
            select(DemoSession)
            .where(DemoSession.telegram_user_id == telegram_user_id)
            .with_for_update()
        )
        return self._validate_workspace(workspace, generation)

    @staticmethod
    def _validate_workspace(
        workspace: DemoSession | None, generation: int | None
    ) -> DemoSession:
        if workspace is None:
            raise DemoError("Сначала отправьте /start, чтобы создать демо.")
        if generation is not None and workspace.generation != generation:
            raise DemoStaleAction("Эта кнопка устарела после сброса. Откройте меню заново.")
        return workspace

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        return current if current.tzinfo is not None else current.replace(tzinfo=UTC)
