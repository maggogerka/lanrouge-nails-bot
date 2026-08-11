"""Role matrix, live membership, bootstrap, and invitation security tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database.models.business import StaffInvitation, StaffMember
from app.database.models.user import User
from app.domain.enums import StaffInvitationStatus, StaffRole
from app.domain.errors import AuthorizationError, EntityNotFoundError
from app.repositories.audit_repository import AuditRepository
from app.repositories.staff_repository import StaffRepository
from app.schemas.authorization import (
    StaffContext,
    StaffIdentity,
    StaffInvitationCreate,
    StaffPermission,
    can_assign_role,
    permissions_for_role,
)
from app.services.authorization_service import AuthorizationService

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)
VALID_TOKEN = "A" * 43


def member(
    role: StaffRole = StaffRole.OWNER,
    *,
    member_id: int = 11,
    business_id: int = 7,
    user_id: int = 21,
    active: bool = True,
    archived_at: datetime | None = None,
    bootstrap: bool = False,
    permission_grants: list[str] | None = None,
) -> StaffMember:
    return StaffMember(
        id=member_id,
        business_id=business_id,
        user_id=user_id,
        display_name="Сотрудник",
        role=role,
        is_active=active,
        is_bookable=role is StaffRole.MASTER,
        is_bootstrap_owner=bootstrap,
        permission_grants=permission_grants or [],
        archived_at=archived_at,
    )


def user(*, user_id: int = 21, telegram_id: int = 101) -> User:
    return User(id=user_id, telegram_id=telegram_id)


def context(
    role: StaffRole = StaffRole.OWNER,
    *,
    bootstrap: bool = False,
    grants: frozenset[StaffPermission] = frozenset(),
) -> StaffContext:
    return StaffContext(
        business_id=7,
        staff_member_id=11,
        user_id=21,
        telegram_id=101,
        display_name="Сотрудник",
        role=role,
        is_bookable=role is StaffRole.MASTER,
        is_bootstrap_owner=bootstrap,
        permission_grants=grants,
    )


def invitation(
    *,
    status: StaffInvitationStatus = StaffInvitationStatus.ACTIVE,
    role: StaffRole = StaffRole.MASTER,
    expires_at: datetime | None = None,
) -> StaffInvitation:
    return StaffInvitation(
        id=31,
        business_id=7,
        token_digest=AuthorizationService.digest_token(VALID_TOKEN),
        role=role,
        display_name="Мастер",
        is_bookable=role is StaffRole.MASTER,
        status=status,
        expires_at=expires_at or NOW + timedelta(hours=24),
        created_by_staff_id=11,
    )


def build_service() -> tuple[
    AuthorizationService,
    MagicMock,
    MagicMock,
    MagicMock,
]:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    repository = MagicMock(spec=StaffRepository)
    audit = MagicMock(spec=AuditRepository)
    session_factory = MagicMock(return_value=session)
    service = AuthorizationService(
        session_factory,
        staff_repository_factory=lambda _: repository,
        audit_repository_factory=lambda _: audit,
    )
    return service, session, repository, audit


def test_role_permission_matrix_is_scoped() -> None:
    assert permissions_for_role(StaffRole.OWNER) == frozenset(StaffPermission)
    assert StaffPermission.MANAGE_PRIVATE_SETTINGS in permissions_for_role(StaffRole.OWNER)
    assert StaffPermission.MANAGE_PRIVATE_SETTINGS not in permissions_for_role(StaffRole.MANAGER)
    assert StaffPermission.MANAGE_STAFF not in permissions_for_role(StaffRole.MANAGER)
    assert StaffPermission.HANDLE_DATA_DELETION not in permissions_for_role(StaffRole.MANAGER)
    assert StaffPermission.EDIT_PAYMENT_INSTRUCTIONS not in permissions_for_role(StaffRole.MANAGER)
    assert StaffPermission.MANAGE_BROADCASTS in permissions_for_role(StaffRole.MANAGER)
    assert StaffPermission.MANAGE_OWN_APPOINTMENTS in permissions_for_role(StaffRole.MASTER)
    assert StaffPermission.VIEW_ALL_APPOINTMENTS not in permissions_for_role(StaffRole.MASTER)
    assert StaffPermission.APPROVE_PREPAYMENTS in permissions_for_role(StaffRole.MASTER)
    assert StaffPermission.REJECT_PREPAYMENTS not in permissions_for_role(StaffRole.MASTER)
    assert StaffPermission.OVERRIDE_BOOKING_LIMIT not in permissions_for_role(StaffRole.MASTER)
    assert StaffPermission.MANAGE_ALL_APPOINTMENTS in permissions_for_role(StaffRole.RECEPTIONIST)
    assert StaffPermission.VIEW_ALL_STATISTICS in permissions_for_role(StaffRole.RECEPTIONIST)
    assert StaffPermission.MANAGE_BROADCASTS not in permissions_for_role(StaffRole.RECEPTIONIST)
    assert StaffPermission.REFUND_PAYMENTS not in permissions_for_role(StaffRole.RECEPTIONIST)
    assert StaffPermission.VIEW_VENDOR_SUPPORT not in permissions_for_role(StaffRole.RECEPTIONIST)


def test_safe_permission_grant_extends_but_never_replaces_role_matrix() -> None:
    target = context(
        StaffRole.MASTER,
        grants=frozenset({StaffPermission.OVERRIDE_BOOKING_LIMIT}),
    )

    assert target.has_permission(StaffPermission.VIEW_OWN_APPOINTMENTS)
    assert target.has_permission(StaffPermission.OVERRIDE_BOOKING_LIMIT)
    assert not target.has_permission(StaffPermission.MANAGE_PRIVATE_SETTINGS)


def test_only_owner_can_assign_privileged_roles() -> None:
    assert not can_assign_role(StaffRole.OWNER, StaffRole.OWNER)
    assert can_assign_role(
        StaffRole.OWNER,
        StaffRole.OWNER,
        actor_is_bootstrap=True,
    )
    assert can_assign_role(StaffRole.MANAGER, StaffRole.MASTER)
    assert can_assign_role(StaffRole.MANAGER, StaffRole.RECEPTIONIST)
    assert not can_assign_role(StaffRole.MANAGER, StaffRole.MANAGER)
    assert not can_assign_role(StaffRole.MASTER, StaffRole.MASTER)


def test_non_master_invitation_cannot_be_bookable() -> None:
    with pytest.raises(ValueError, match="only a master"):
        StaffInvitationCreate(
            role=StaffRole.MANAGER,
            display_name="Manager",
            is_bookable=True,
        )


@pytest.mark.asyncio
async def test_authorize_uses_live_database_membership_and_permission() -> None:
    service, _, repository, _ = build_service()
    repository.get_by_telegram_id = AsyncMock(return_value=(member(StaffRole.MASTER), user()))

    staff = await service.authorize(
        business_id=7,
        telegram_id=101,
        permission=StaffPermission.VIEW_OWN_APPOINTMENTS,
    )

    assert staff.role is StaffRole.MASTER
    repository.get_by_telegram_id.assert_awaited_once_with(7, 101)

    with pytest.raises(AuthorizationError, match="Недостаточно прав"):
        await service.authorize(
            business_id=7,
            telegram_id=101,
            permission=StaffPermission.VIEW_ALL_APPOINTMENTS,
        )


@pytest.mark.asyncio
async def test_list_active_staff_uses_business_scoped_roles() -> None:
    service, _, repository, _ = build_service()
    target_member = member(StaffRole.RECEPTIONIST)
    target_user = user(user_id=44, telegram_id=555)
    repository.list_active_by_roles = AsyncMock(return_value=[(target_member, target_user)])

    result = await service.list_active_staff(
        business_id=7,
        roles={StaffRole.OWNER, StaffRole.RECEPTIONIST},
    )

    assert len(result) == 1
    assert result[0].staff_member_id == target_member.id
    assert result[0].telegram_id == 555
    repository.list_active_by_roles.assert_awaited_once_with(
        7,
        frozenset({StaffRole.OWNER, StaffRole.RECEPTIONIST}),
    )


@pytest.mark.asyncio
async def test_missing_or_revoked_membership_is_denied() -> None:
    service, _, repository, _ = build_service()
    repository.get_by_telegram_id = AsyncMock(return_value=None)

    with pytest.raises(AuthorizationError, match="отозван"):
        await service.authorize(business_id=7, telegram_id=101)


@pytest.mark.asyncio
async def test_invitation_stores_only_digest_and_audits_safe_metadata() -> None:
    service, session, repository, audit = build_service()
    repository.get_by_telegram_id = AsyncMock(return_value=(member(), user()))

    async def add_invitation(value: StaffInvitation) -> StaffInvitation:
        value.id = 31
        return value

    repository.add_invitation = AsyncMock(side_effect=add_invitation)
    audit.add = AsyncMock()

    with patch(
        "app.services.authorization_service.secrets.token_urlsafe",
        return_value=VALID_TOKEN,
    ):
        result = await service.issue_invitation(
            context(),
            StaffInvitationCreate(
                role=StaffRole.MASTER,
                display_name="  Новый мастер  ",
                is_bookable=True,
            ),
            now=NOW,
            correlation_id="invite-issue",
        )

    persisted = repository.add_invitation.await_args.args[0]
    assert result.token.get_secret_value() == VALID_TOKEN
    assert persisted.token_digest == AuthorizationService.digest_token(VALID_TOKEN)
    assert persisted.token_digest != VALID_TOKEN
    assert persisted.display_name == "Новый мастер"
    assert persisted.expires_at == NOW + timedelta(hours=24)
    audit_changes = audit.add.await_args.kwargs["changes"]
    assert all("token" not in key for key in audit_changes)
    assert audit.add.await_args.kwargs["correlation_id"] == "invite-issue"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_context_cannot_issue_role_forbidden_by_live_membership() -> None:
    service, _, repository, _ = build_service()
    repository.get_by_telegram_id = AsyncMock(return_value=(member(StaffRole.MANAGER), user()))

    with pytest.raises(AuthorizationError, match="Недостаточно прав"):
        await service.issue_invitation(
            context(StaffRole.OWNER),
            StaffInvitationCreate(role=StaffRole.OWNER, display_name="Ещё один владелец"),
            now=NOW,
        )

    repository.add_invitation.assert_not_awaited()


@pytest.mark.asyncio
async def test_accept_invitation_locks_row_and_binds_first_telegram_user() -> None:
    service, session, repository, audit = build_service()
    target_invitation = invitation()
    repository.get_invitation_by_digest = AsyncMock(return_value=target_invitation)
    repository.get_or_create_user = AsyncMock(return_value=user(user_id=44, telegram_id=555))
    repository.get_by_user_id = AsyncMock(return_value=None)

    async def add_member(value: StaffMember) -> StaffMember:
        value.id = 88
        return value

    repository.add = AsyncMock(side_effect=add_member)
    repository.flush = AsyncMock()
    audit.add = AsyncMock()

    result = await service.accept_invitation(
        VALID_TOKEN,
        StaffIdentity(telegram_id=555, username="new_master", first_name="Анна"),
        now=NOW,
        correlation_id="invite-accept",
    )

    repository.get_invitation_by_digest.assert_awaited_once_with(
        AuthorizationService.digest_token(VALID_TOKEN),
        for_update=True,
    )
    repository.get_by_user_id.assert_awaited_once_with(7, 44, for_update=True)
    assert result.staff.staff_member_id == 88
    assert result.staff.telegram_id == 555
    assert result.staff.role is StaffRole.MASTER
    assert target_invitation.status is StaffInvitationStatus.USED
    assert target_invitation.accepted_by_user_id == 44
    assert target_invitation.used_at == NOW
    assert "token" not in audit.add.await_args.kwargs["changes"]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_used_invitation_cannot_be_reused() -> None:
    service, session, repository, _ = build_service()
    repository.get_invitation_by_digest = AsyncMock(
        return_value=invitation(status=StaffInvitationStatus.USED)
    )

    with pytest.raises(AuthorizationError, match="недействительно"):
        await service.accept_invitation(
            VALID_TOKEN,
            StaffIdentity(telegram_id=555),
            now=NOW,
        )

    repository.get_or_create_user.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_invitation_is_persisted_and_rejected() -> None:
    service, session, repository, audit = build_service()
    target_invitation = invitation(expires_at=NOW - timedelta(seconds=1))
    repository.get_invitation_by_digest = AsyncMock(return_value=target_invitation)
    repository.flush = AsyncMock()
    audit.add = AsyncMock()

    with pytest.raises(AuthorizationError, match="истекло"):
        await service.accept_invitation(
            VALID_TOKEN,
            StaffIdentity(telegram_id=555),
            now=NOW,
        )

    assert target_invitation.status is StaffInvitationStatus.EXPIRED
    repository.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    repository.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_invitation_uses_live_actor_and_row_lock() -> None:
    service, session, repository, audit = build_service()
    repository.get_by_telegram_id = AsyncMock(return_value=(member(), user()))
    target_invitation = invitation()
    repository.get_invitation_by_id = AsyncMock(return_value=target_invitation)
    repository.flush = AsyncMock()
    audit.add = AsyncMock()

    result = await service.revoke_invitation(context(), 31, now=NOW)

    repository.get_invitation_by_id.assert_awaited_once_with(7, 31, for_update=True)
    assert result.invitation_id == 31
    assert target_invitation.status is StaffInvitationStatus.REVOKED
    assert target_invitation.revoked_by_staff_id == 11
    assert target_invitation.revoked_at == NOW
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_staff_screen_is_live_authorized_and_returns_no_telegram_identity() -> None:
    service, _, repository, _ = build_service()
    repository.get_by_telegram_id = AsyncMock(return_value=(member(), user()))
    repository.list_members = AsyncMock(
        return_value=[member(StaffRole.MASTER, member_id=12, user_id=22)]
    )

    result = await service.list_staff(context())

    repository.get_by_telegram_id.assert_awaited_once_with(7, 101)
    repository.list_members.assert_awaited_once_with(7)
    assert result[0].display_name == "Сотрудник"
    assert result[0].role is StaffRole.MASTER
    assert result[0].is_bound
    assert "telegram" not in result[0].model_dump()


@pytest.mark.asyncio
async def test_pending_invitation_list_never_returns_secret_digest() -> None:
    service, _, repository, _ = build_service()
    repository.get_by_telegram_id = AsyncMock(return_value=(member(), user()))
    repository.list_active_invitations = AsyncMock(return_value=[invitation()])

    result = await service.list_active_invitations(context(), now=NOW)

    repository.list_active_invitations.assert_awaited_once_with(7, now=NOW)
    assert result[0].id == 31
    assert "token" not in result[0].model_dump()


@pytest.mark.asyncio
async def test_manager_cannot_revoke_owner_invitation() -> None:
    service, _, repository, _ = build_service()
    repository.get_by_telegram_id = AsyncMock(return_value=(member(StaffRole.MANAGER), user()))
    repository.get_invitation_by_id = AsyncMock(return_value=invitation(role=StaffRole.OWNER))

    with pytest.raises(AuthorizationError, match="Недостаточно прав"):
        await service.revoke_invitation(context(StaffRole.MANAGER), 31, now=NOW)


@pytest.mark.asyncio
async def test_bootstrap_creates_owner_only_when_none_exists() -> None:
    service, session, repository, audit = build_service()
    repository.get_business_for_update = AsyncMock(return_value=SimpleNamespace(id=7))
    repository.get_bootstrap_owner = AsyncMock(return_value=None)
    repository.get_by_telegram_id = AsyncMock(return_value=None)
    repository.get_or_create_user = AsyncMock(return_value=user(user_id=44, telegram_id=555))

    async def add_member(value: StaffMember) -> StaffMember:
        value.id = 88
        return value

    repository.add = AsyncMock(side_effect=add_member)
    audit.add = AsyncMock()

    result = await service.bootstrap_owners(
        business_id=7,
        telegram_ids=[555],
        now=NOW,
    )

    repository.get_business_for_update.assert_awaited_once_with(7)
    assert not result.owner_already_present
    assert result.created[0].role is StaffRole.OWNER
    assert result.created[0].telegram_id == 555
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_rejects_archived_configured_identity() -> None:
    service, session, repository, audit = build_service()
    repository.get_business_for_update = AsyncMock(return_value=SimpleNamespace(id=7))
    repository.get_bootstrap_owner = AsyncMock(return_value=None)
    archived = member(active=False, archived_at=NOW - timedelta(days=1))
    repository.get_by_telegram_id = AsyncMock(return_value=(archived, user()))
    audit.add = AsyncMock()

    with pytest.raises(AuthorizationError, match="отозванной"):
        await service.bootstrap_owners(
            business_id=7,
            telegram_ids=[101],
            now=NOW,
        )

    assert not archived.is_active
    repository.get_or_create_user.assert_not_awaited()
    repository.add.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_binds_the_configured_active_owner() -> None:
    service, session, repository, audit = build_service()
    repository.get_business_for_update = AsyncMock(return_value=SimpleNamespace(id=7))
    configured = member(StaffRole.OWNER)
    repository.get_bootstrap_owner = AsyncMock(return_value=None)
    repository.get_by_telegram_id = AsyncMock(return_value=(configured, user()))
    repository.flush = AsyncMock()
    audit.add = AsyncMock()

    result = await service.bootstrap_owners(
        business_id=7,
        telegram_ids=[101],
        now=NOW,
    )

    assert not result.owner_already_present
    assert not result.created
    assert configured.is_bootstrap_owner
    repository.get_by_telegram_id.assert_awaited_once()
    repository.add.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_bootstrap_cannot_be_replaced_by_configuration() -> None:
    service, session, repository, _ = build_service()
    repository.get_business_for_update = AsyncMock(return_value=SimpleNamespace(id=7))
    repository.get_bootstrap_owner = AsyncMock(
        return_value=member(StaffRole.OWNER, member_id=99, bootstrap=True)
    )
    repository.get_by_telegram_id = AsyncMock(return_value=(member(), user()))

    with pytest.raises(AuthorizationError, match="не совпадает"):
        await service.bootstrap_owners(business_id=7, telegram_ids=[101], now=NOW)

    repository.add.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_member_cannot_be_revoked_even_by_itself() -> None:
    service, session, repository, _ = build_service()
    bootstrap = member(StaffRole.OWNER, bootstrap=True)
    repository.get_by_telegram_id = AsyncMock(return_value=(bootstrap, user()))
    repository.get_by_id = AsyncMock(return_value=bootstrap)

    with pytest.raises(AuthorizationError, match="Bootstrap"):
        await service.revoke_member(context(bootstrap=True), bootstrap.id, now=NOW)

    repository.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_bootstrap_can_revoke_an_invited_owner() -> None:
    service, session, repository, audit = build_service()
    target = member(StaffRole.OWNER, member_id=44, user_id=55)
    repository.get_by_id = AsyncMock(return_value=target)
    repository.flush = AsyncMock()
    audit.add = AsyncMock()
    repository.get_by_telegram_id = AsyncMock(return_value=(member(), user()))

    with pytest.raises(AuthorizationError, match="только bootstrap"):
        await service.revoke_member(context(), target.id, now=NOW)

    repository.get_by_telegram_id = AsyncMock(return_value=(member(bootstrap=True), user()))
    result = await service.revoke_member(context(bootstrap=True), target.id, now=NOW)

    assert not result.is_active
    assert target.archived_at == NOW
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_can_grant_only_safe_permission_to_non_owner() -> None:
    service, session, repository, audit = build_service()
    target = member(StaffRole.MASTER, member_id=44, user_id=55)
    repository.get_by_telegram_id = AsyncMock(return_value=(member(), user()))
    repository.get_by_id = AsyncMock(return_value=target)
    repository.flush = AsyncMock()
    audit.add = AsyncMock()

    result = await service.set_permission_grant(
        context(),
        target.id,
        StaffPermission.OVERRIDE_BOOKING_LIMIT,
        enabled=True,
    )

    assert StaffPermission.OVERRIDE_BOOKING_LIMIT in result.permission_grants
    assert target.permission_grants == [StaffPermission.OVERRIDE_BOOKING_LIMIT.value]
    session.commit.assert_awaited_once()

    with pytest.raises(AuthorizationError, match="нельзя выдавать"):
        await service.set_permission_grant(
            context(),
            target.id,
            StaffPermission.MANAGE_PRIVATE_SETTINGS,
            enabled=True,
        )


@pytest.mark.asyncio
async def test_bootstrap_rejects_unknown_business() -> None:
    service, _, repository, _ = build_service()
    repository.get_business_for_update = AsyncMock(return_value=None)

    with pytest.raises(EntityNotFoundError, match="Бизнес"):
        await service.bootstrap_owners(
            business_id=999,
            telegram_ids=[101],
            now=NOW,
        )
