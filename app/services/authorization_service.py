"""DB-backed staff authorization, bootstrap, and one-time invitations."""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.business import StaffInvitation, StaffMember
from app.domain.enums import StaffInvitationStatus, StaffRole
from app.domain.errors import AuthorizationError, EntityNotFoundError, StaffReassignmentError
from app.repositories.audit_repository import AuditRepository
from app.repositories.staff_repository import StaffRepository
from app.schemas.authorization import (
    GRANTABLE_STAFF_PERMISSIONS,
    AcceptedStaffInvitation,
    IssuedStaffInvitation,
    RevokedStaffInvitation,
    StaffBootstrapResult,
    StaffContext,
    StaffIdentity,
    StaffInvitationCreate,
    StaffInvitationView,
    StaffMemberView,
    StaffPermission,
    can_assign_role,
)

SessionFactory = async_sessionmaker[AsyncSession]
StaffRepositoryFactory = Callable[[AsyncSession], StaffRepository]
AuditRepositoryFactory = Callable[[AsyncSession], AuditRepository]

_INVITATION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,200}\Z")
_INVALID_INVITATION_MESSAGE = "Приглашение недействительно, уже использовано или истекло."


class AuthorizationService:
    """Resolve live staff authority instead of trusting a handler-provided role."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        staff_repository_factory: StaffRepositoryFactory = StaffRepository,
        audit_repository_factory: AuditRepositoryFactory = AuditRepository,
        invitation_token_bytes: int = 32,
    ) -> None:
        if invitation_token_bytes < 24:
            raise ValueError("invitation_token_bytes must provide at least 192 bits")
        self._session_factory = session_factory
        self._staff_repository_factory = staff_repository_factory
        self._audit_repository_factory = audit_repository_factory
        self._invitation_token_bytes = invitation_token_bytes

    async def authorize(
        self,
        *,
        business_id: int,
        telegram_id: int,
        permission: StaffPermission | None = None,
    ) -> StaffContext:
        """Load current role/activity from the database for every protected action."""

        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            context = await self._live_context(repository, business_id, telegram_id)
            self._require_permission(context, permission)
            return context

    async def list_active_staff(
        self,
        *,
        business_id: int,
        roles: Iterable[StaffRole],
    ) -> tuple[StaffContext, ...]:
        """Resolve current notification recipients without environment-ID leakage."""

        normalized_roles = frozenset(roles)
        if not normalized_roles:
            return ()
        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            rows = await repository.list_active_by_roles(business_id, normalized_roles)
            return tuple(self._context(member, user.id, user.telegram_id) for member, user in rows)

    async def list_staff(self, actor: StaffContext) -> tuple[StaffMemberView, ...]:
        """List tenant members after a fresh DB authorization check."""

        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            live_actor = await self._live_context(
                repository,
                actor.business_id,
                actor.telegram_id,
            )
            self._require_permission(live_actor, StaffPermission.VIEW_STAFF)
            members = await repository.list_members(live_actor.business_id)
            return tuple(
                StaffMemberView(
                    id=member.id,
                    display_name=member.display_name,
                    role=member.role,
                    is_active=member.is_active and member.archived_at is None,
                    is_bookable=member.is_bookable,
                    is_bootstrap_owner=bool(member.is_bootstrap_owner),
                    permission_grants=self._permission_grants(member),
                    is_bound=member.user_id is not None,
                    archived_at=member.archived_at,
                )
                for member in members
            )

    async def list_active_invitations(
        self,
        actor: StaffContext,
        *,
        now: datetime | None = None,
    ) -> tuple[StaffInvitationView, ...]:
        """List revocable pending invitations without returning token digests."""

        current = self._aware_now(now)
        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            live_actor = await self._live_context(
                repository,
                actor.business_id,
                actor.telegram_id,
            )
            self._require_permission(live_actor, StaffPermission.VIEW_STAFF)
            invitations = await repository.list_active_invitations(
                live_actor.business_id,
                now=current,
            )
            return tuple(
                StaffInvitationView(
                    id=invitation.id,
                    role=invitation.role,
                    display_name=invitation.display_name,
                    is_bookable=invitation.is_bookable,
                    expires_at=invitation.expires_at,
                )
                for invitation in invitations
            )

    async def issue_invitation(
        self,
        actor: StaffContext,
        values: StaffInvitationCreate,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> IssuedStaffInvitation:
        """Return a high-entropy token once while persisting only its SHA-256 digest."""

        current = self._aware_now(now)
        token = secrets.token_urlsafe(self._invitation_token_bytes)
        digest = self.digest_token(token)
        expires_at = current + timedelta(hours=values.expires_in_hours)
        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            audit = self._audit_repository_factory(session)
            live_actor = await self._live_context(
                repository,
                actor.business_id,
                actor.telegram_id,
            )
            self._require_permission(live_actor, StaffPermission.INVITE_STAFF)
            self._require_assignable_role(live_actor, values.role)
            invitation = await repository.add_invitation(
                StaffInvitation(
                    business_id=live_actor.business_id,
                    token_digest=digest,
                    role=values.role,
                    display_name=values.display_name,
                    is_bookable=values.is_bookable,
                    status=StaffInvitationStatus.ACTIVE,
                    expires_at=expires_at,
                    created_by_staff_id=live_actor.staff_member_id,
                )
            )
            await audit.add(
                business_id=live_actor.business_id,
                actor_user_id=live_actor.user_id,
                action="staff_invitation.issued",
                entity_type="staff_invitation",
                entity_id=str(invitation.id),
                changes={
                    "business_id": live_actor.business_id,
                    "role": values.role.value,
                    "is_bookable": values.is_bookable,
                    "expires_at": expires_at.isoformat(),
                },
                correlation_id=correlation_id,
            )
            await session.commit()
            return IssuedStaffInvitation(
                invitation_id=invitation.id,
                business_id=invitation.business_id,
                role=invitation.role,
                display_name=invitation.display_name,
                is_bookable=invitation.is_bookable,
                expires_at=invitation.expires_at,
                token=token,
            )

    async def accept_invitation(
        self,
        token: str,
        identity: StaffIdentity,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AcceptedStaffInvitation:
        """Atomically consume a token and bind it to the first Telegram identity."""

        if _INVITATION_TOKEN_PATTERN.fullmatch(token) is None:
            raise AuthorizationError(_INVALID_INVITATION_MESSAGE)
        current = self._aware_now(now)
        digest = self.digest_token(token)
        expired = False
        accepted: AcceptedStaffInvitation | None = None
        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            audit = self._audit_repository_factory(session)
            invitation = await repository.get_invitation_by_digest(digest, for_update=True)
            if invitation is None or invitation.status is not StaffInvitationStatus.ACTIVE:
                raise AuthorizationError(_INVALID_INVITATION_MESSAGE)
            if invitation.expires_at <= current:
                invitation.status = StaffInvitationStatus.EXPIRED
                await repository.flush()
                await audit.add(
                    business_id=invitation.business_id,
                    actor_user_id=None,
                    action="staff_invitation.expired",
                    entity_type="staff_invitation",
                    entity_id=str(invitation.id),
                    changes={"business_id": invitation.business_id},
                    correlation_id=correlation_id,
                )
                await session.commit()
                expired = True
            else:
                user = await repository.get_or_create_user(identity)
                existing = await repository.get_by_user_id(
                    invitation.business_id,
                    user.id,
                    for_update=True,
                )
                if existing is not None:
                    raise AuthorizationError(
                        "Этот Telegram-пользователь уже связан с сотрудником бизнеса."
                    )
                member = await repository.add(
                    StaffMember(
                        business_id=invitation.business_id,
                        user_id=user.id,
                        display_name=invitation.display_name,
                        role=invitation.role,
                        is_active=True,
                        is_bookable=invitation.is_bookable,
                    )
                )
                invitation.status = StaffInvitationStatus.USED
                invitation.accepted_by_user_id = user.id
                invitation.used_at = current
                await repository.flush()
                context = self._context(member, user.id, user.telegram_id)
                await audit.add(
                    business_id=invitation.business_id,
                    actor_user_id=user.id,
                    action="staff_invitation.accepted",
                    entity_type="staff_invitation",
                    entity_id=str(invitation.id),
                    changes={
                        "business_id": invitation.business_id,
                        "staff_member_id": member.id,
                        "role": member.role.value,
                    },
                    correlation_id=correlation_id,
                )
                await session.commit()
                accepted = AcceptedStaffInvitation(
                    invitation_id=invitation.id,
                    accepted_at=current,
                    staff=context,
                )
        if expired:
            raise AuthorizationError(_INVALID_INVITATION_MESSAGE)
        if accepted is None:
            raise RuntimeError("invitation acceptance produced no result")
        return accepted

    async def revoke_invitation(
        self,
        actor: StaffContext,
        invitation_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> RevokedStaffInvitation:
        """Revoke an active invitation under a row lock and a fresh role check."""

        current = self._aware_now(now)
        expired = False
        result: RevokedStaffInvitation | None = None
        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            audit = self._audit_repository_factory(session)
            live_actor = await self._live_context(
                repository,
                actor.business_id,
                actor.telegram_id,
            )
            self._require_permission(live_actor, StaffPermission.INVITE_STAFF)
            invitation = await repository.get_invitation_by_id(
                live_actor.business_id,
                invitation_id,
                for_update=True,
            )
            if invitation is None or invitation.status is not StaffInvitationStatus.ACTIVE:
                raise EntityNotFoundError("Активное приглашение не найдено.")
            self._require_assignable_role(live_actor, invitation.role)
            if invitation.expires_at <= current:
                invitation.status = StaffInvitationStatus.EXPIRED
                await repository.flush()
                await session.commit()
                expired = True
            else:
                invitation.status = StaffInvitationStatus.REVOKED
                invitation.revoked_at = current
                invitation.revoked_by_staff_id = live_actor.staff_member_id
                await repository.flush()
                await audit.add(
                    business_id=invitation.business_id,
                    actor_user_id=live_actor.user_id,
                    action="staff_invitation.revoked",
                    entity_type="staff_invitation",
                    entity_id=str(invitation.id),
                    changes={
                        "business_id": invitation.business_id,
                        "role": invitation.role.value,
                    },
                    correlation_id=correlation_id,
                )
                await session.commit()
                result = RevokedStaffInvitation(
                    invitation_id=invitation.id,
                    business_id=invitation.business_id,
                    role=invitation.role,
                    revoked_at=current,
                )
        if expired:
            raise EntityNotFoundError("Активное приглашение не найдено.")
        if result is None:
            raise RuntimeError("invitation revocation produced no result")
        return result

    async def revoke_member(
        self,
        actor: StaffContext,
        staff_member_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> StaffMemberView:
        """Revoke an ordinary membership without deleting historical foreign keys."""

        current = self._aware_now(now)
        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            audit = self._audit_repository_factory(session)
            live_actor = await self._live_context(
                repository,
                actor.business_id,
                actor.telegram_id,
            )
            self._require_permission(live_actor, StaffPermission.MANAGE_STAFF)
            target = await repository.get_by_id(
                live_actor.business_id,
                staff_member_id,
                for_update=True,
            )
            if target is None:
                raise EntityNotFoundError("Сотрудник не найден.")
            self._require_manageable_target(live_actor, target)
            target.is_active = False
            target.is_bookable = False
            target.archived_at = current
            target.permission_grants = []
            await repository.flush()
            await audit.add(
                business_id=live_actor.business_id,
                actor_user_id=live_actor.user_id,
                action="staff.role_revoked",
                entity_type="staff_member",
                entity_id=str(target.id),
                changes={"previous_role": target.role.value},
                correlation_id=correlation_id,
            )
            await session.commit()
            return self._member_view(target)

    async def change_member_role(
        self,
        actor: StaffContext,
        staff_member_id: int,
        role: StaffRole,
        *,
        correlation_id: str | None = None,
    ) -> StaffMemberView:
        """Change a live membership while preserving the owner hierarchy."""

        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            audit = self._audit_repository_factory(session)
            live_actor = await self._live_context(
                repository,
                actor.business_id,
                actor.telegram_id,
            )
            self._require_permission(live_actor, StaffPermission.MANAGE_STAFF)
            target = await repository.get_by_id(
                live_actor.business_id,
                staff_member_id,
                for_update=True,
            )
            if target is None:
                raise EntityNotFoundError("Сотрудник не найден.")
            self._require_manageable_target(live_actor, target)
            self._require_assignable_role(live_actor, role)
            previous = target.role
            target.role = role
            if role is not StaffRole.MASTER and not target.is_bootstrap_owner:
                target.is_bookable = False
            await repository.flush()
            await audit.add(
                business_id=live_actor.business_id,
                actor_user_id=live_actor.user_id,
                action="staff.role_changed",
                entity_type="staff_member",
                entity_id=str(target.id),
                changes={"previous_role": previous.value, "role": role.value},
                correlation_id=correlation_id,
            )
            await session.commit()
            return self._member_view(target)

    async def set_permission_grant(
        self,
        actor: StaffContext,
        staff_member_id: int,
        permission: StaffPermission,
        *,
        enabled: bool,
        correlation_id: str | None = None,
    ) -> StaffMemberView:
        """Grant only explicitly safe capabilities; bootstrap authority is never editable."""

        if permission not in GRANTABLE_STAFF_PERMISSIONS:
            raise AuthorizationError("Это разрешение нельзя выдавать отдельно.")
        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            audit = self._audit_repository_factory(session)
            live_actor = await self._live_context(
                repository,
                actor.business_id,
                actor.telegram_id,
            )
            if live_actor.role is not StaffRole.OWNER:
                raise AuthorizationError("Только владелец может менять дополнительные разрешения.")
            target = await repository.get_by_id(
                live_actor.business_id,
                staff_member_id,
                for_update=True,
            )
            if target is None:
                raise EntityNotFoundError("Сотрудник не найден.")
            self._require_manageable_target(live_actor, target)
            grants = self._permission_grants(target)
            updated = grants | {permission} if enabled else grants - {permission}
            target.permission_grants = sorted(item.value for item in updated)
            await repository.flush()
            await audit.add(
                business_id=live_actor.business_id,
                actor_user_id=live_actor.user_id,
                action="staff.permission_grant_changed",
                entity_type="staff_member",
                entity_id=str(target.id),
                changes={"permission": permission.value, "enabled": enabled},
                correlation_id=correlation_id,
            )
            await session.commit()
            return self._member_view(target)

    async def reassign_future_appointments(
        self,
        actor: StaffContext,
        source_staff_member_id: int,
        target_staff_member_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> int:
        """Safely move future bookings to matching target windows, preserving history."""

        if source_staff_member_id == target_staff_member_id:
            raise StaffReassignmentError("Исходный и новый специалист совпадают.")
        current = self._aware_now(now)
        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            audit = self._audit_repository_factory(session)
            live_actor = await self._live_context(
                repository,
                actor.business_id,
                actor.telegram_id,
            )
            self._require_permission(live_actor, StaffPermission.MANAGE_STAFF)
            source = await repository.get_by_id(
                live_actor.business_id,
                source_staff_member_id,
                for_update=True,
            )
            target = await repository.get_by_id(
                live_actor.business_id,
                target_staff_member_id,
                for_update=True,
            )
            if source is None or target is None:
                raise EntityNotFoundError("Сотрудник не найден.")
            self._require_manageable_target(live_actor, source)
            if not target.is_active or target.archived_at is not None or not target.is_bookable:
                raise StaffReassignmentError(
                    "Новый специалист неактивен или недоступен для записи."
                )
            try:
                count = await repository.reassign_future_appointments(
                    live_actor.business_id,
                    source.id,
                    target,
                    now=current,
                )
            except ValueError as exc:
                messages = {
                    "target_missing_service_assignments": (
                        "Сначала назначьте новому специалисту все услуги переносимых записей."
                    ),
                    "target_missing_exact_windows": (
                        "Сначала создайте новому специалисту свободные окна на то же время."
                    ),
                    "source_window_missing": "Исходное окно записи не найдено.",
                }
                raise StaffReassignmentError(messages.get(str(exc), "Перенос невозможен.")) from exc
            await audit.add(
                business_id=live_actor.business_id,
                actor_user_id=live_actor.user_id,
                action="staff.future_appointments_reassigned",
                entity_type="staff_member",
                entity_id=str(source.id),
                changes={"target_staff_member_id": target.id, "appointment_count": count},
                correlation_id=correlation_id,
            )
            await session.commit()
            return count

    async def bootstrap_owners(
        self,
        *,
        business_id: int,
        telegram_ids: Iterable[int],
        display_name: str = "Владелец",
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> StaffBootstrapResult:
        """Bind one immutable bootstrap owner and optionally seed ordinary owners once."""

        current = self._aware_now(now)
        normalized_ids = tuple(dict.fromkeys(telegram_ids))
        if not normalized_ids:
            raise ValueError("at least one bootstrap Telegram ID is required")
        if any(value <= 0 for value in normalized_ids):
            raise ValueError("bootstrap Telegram IDs must be positive integers")
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("bootstrap display name must not be empty")

        async with self._session_factory() as session:
            repository = self._staff_repository_factory(session)
            audit = self._audit_repository_factory(session)
            business = await repository.get_business_for_update(business_id)
            if business is None:
                raise EntityNotFoundError("Бизнес не найден.")
            created: list[StaffContext] = []
            skipped = 0
            existing_bootstrap = await repository.get_bootstrap_owner(
                business_id,
                for_update=True,
            )
            bootstrap_id = normalized_ids[0]
            configured_bootstrap = await repository.get_by_telegram_id(
                business_id,
                bootstrap_id,
                active_only=False,
                for_update=True,
            )
            if existing_bootstrap is not None:
                if (
                    configured_bootstrap is None
                    or configured_bootstrap[0].id != existing_bootstrap.id
                ):
                    raise AuthorizationError(
                        "Настроенный bootstrap ID не совпадает "
                        "с неизменяемым владельцем экземпляра."
                    )
            elif configured_bootstrap is not None:
                bootstrap_member, bootstrap_user = configured_bootstrap
                if (
                    bootstrap_member.role is not StaffRole.OWNER
                    or not bootstrap_member.is_active
                    or bootstrap_member.archived_at is not None
                    or bootstrap_user.is_blocked
                ):
                    raise AuthorizationError(
                        "Настроенный bootstrap ID связан с отозванной или заблокированной ролью."
                    )
                bootstrap_member.is_bootstrap_owner = True
                bootstrap_member.permission_grants = []
                await repository.flush()
                await audit.add(
                    business_id=business_id,
                    actor_user_id=bootstrap_user.id,
                    action="staff.bootstrap_owner_bound",
                    entity_type="staff_member",
                    entity_id=str(bootstrap_member.id),
                    changes={"business_id": business_id},
                    correlation_id=correlation_id,
                )
            else:
                bootstrap_user = await repository.get_or_create_user(
                    StaffIdentity(telegram_id=bootstrap_id)
                )
                if bootstrap_user.is_blocked:
                    raise AuthorizationError("Bootstrap-владелец не может быть заблокирован.")
                bootstrap_member = await repository.add(
                    StaffMember(
                        business_id=business_id,
                        user_id=bootstrap_user.id,
                        display_name=normalized_name,
                        role=StaffRole.OWNER,
                        is_active=True,
                        is_bookable=False,
                        is_bootstrap_owner=True,
                    )
                )
                created.append(
                    self._context(
                        bootstrap_member,
                        bootstrap_user.id,
                        bootstrap_user.telegram_id,
                    )
                )
                await audit.add(
                    business_id=business_id,
                    actor_user_id=None,
                    action="staff.bootstrap_owner_created",
                    entity_type="staff_member",
                    entity_id=str(bootstrap_member.id),
                    changes={
                        "business_id": business_id,
                        "role": StaffRole.OWNER.value,
                        "created_at": current.isoformat(),
                    },
                    correlation_id=correlation_id,
                )

            for index, telegram_id in enumerate(normalized_ids[1:], start=2):
                existing = await repository.get_by_telegram_id(
                    business_id,
                    telegram_id,
                    active_only=False,
                    for_update=True,
                )
                if existing is not None:
                    skipped += 1
                    continue
                user = await repository.get_or_create_user(StaffIdentity(telegram_id=telegram_id))
                member = await repository.add(
                    StaffMember(
                        business_id=business_id,
                        user_id=user.id,
                        display_name=f"{normalized_name} {index}",
                        role=StaffRole.OWNER,
                        is_active=True,
                        is_bookable=False,
                        is_bootstrap_owner=False,
                    )
                )
                context = self._context(member, user.id, user.telegram_id)
                created.append(context)
                await audit.add(
                    business_id=business_id,
                    actor_user_id=None,
                    action="staff.configured_owner_created",
                    entity_type="staff_member",
                    entity_id=str(member.id),
                    changes={
                        "business_id": business_id,
                        "role": StaffRole.OWNER.value,
                        "created_at": current.isoformat(),
                    },
                    correlation_id=correlation_id,
                )
            await session.commit()
            return StaffBootstrapResult(
                business_id=business_id,
                owner_already_present=existing_bootstrap is not None,
                created=tuple(created),
                skipped_existing_count=skipped,
            )

    @staticmethod
    def digest_token(token: str) -> str:
        """Derive the only invitation secret representation allowed in persistence."""

        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @classmethod
    async def _live_context(
        cls,
        repository: StaffRepository,
        business_id: int,
        telegram_id: int,
    ) -> StaffContext:
        row = await repository.get_by_telegram_id(business_id, telegram_id)
        if row is None:
            raise AuthorizationError("Доступ сотрудника не найден или отозван.")
        member, user = row
        return cls._context(member, user.id, user.telegram_id)

    @staticmethod
    def _context(member: StaffMember, user_id: int, telegram_id: int) -> StaffContext:
        return StaffContext(
            business_id=member.business_id,
            staff_member_id=member.id,
            user_id=user_id,
            telegram_id=telegram_id,
            display_name=member.display_name,
            role=member.role,
            is_bookable=member.is_bookable,
            is_bootstrap_owner=bool(member.is_bootstrap_owner),
            permission_grants=AuthorizationService._permission_grants(member),
        )

    @staticmethod
    def _permission_grants(member: StaffMember) -> frozenset[StaffPermission]:
        return frozenset(StaffPermission(value) for value in (member.permission_grants or []))

    @classmethod
    def _member_view(cls, member: StaffMember) -> StaffMemberView:
        return StaffMemberView(
            id=member.id,
            display_name=member.display_name,
            role=member.role,
            is_active=member.is_active and member.archived_at is None,
            is_bookable=member.is_bookable,
            is_bootstrap_owner=bool(member.is_bootstrap_owner),
            permission_grants=cls._permission_grants(member),
            is_bound=member.user_id is not None,
            archived_at=member.archived_at,
        )

    @staticmethod
    def _require_manageable_target(actor: StaffContext, target: StaffMember) -> None:
        if target.id == actor.staff_member_id or target.is_bootstrap_owner:
            raise AuthorizationError("Bootstrap-владельца и собственную роль изменять нельзя.")
        if target.role is StaffRole.OWNER:
            if not actor.is_bootstrap_owner:
                raise AuthorizationError("Управлять другими владельцами может только bootstrap.")
            return
        if actor.role is StaffRole.OWNER:
            return
        if actor.role is StaffRole.MANAGER and target.role in {
            StaffRole.MASTER,
            StaffRole.RECEPTIONIST,
        }:
            return
        raise AuthorizationError("Недостаточно прав для изменения этого сотрудника.")

    @staticmethod
    def _require_permission(
        actor: StaffContext,
        permission: StaffPermission | None,
    ) -> None:
        if permission is not None and not actor.has_permission(permission):
            raise AuthorizationError("Недостаточно прав для этого действия.")

    @staticmethod
    def _require_assignable_role(actor: StaffContext, target_role: StaffRole) -> None:
        if not can_assign_role(
            actor.role,
            target_role,
            actor_is_bootstrap=actor.is_bootstrap_owner,
        ):
            raise AuthorizationError("Нельзя назначить выбранную роль.")

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
