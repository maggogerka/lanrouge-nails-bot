"""Server-derived staff identities and the v0.4 permission matrix."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.domain.enums import StaffRole


class StaffPermission(StrEnum):
    """Fine-grained permissions checked by application services."""

    VIEW_BUSINESS = "view_business"
    MANAGE_BUSINESS = "manage_business"
    MANAGE_PRIVATE_SETTINGS = "manage_private_settings"
    VIEW_STAFF = "view_staff"
    INVITE_STAFF = "invite_staff"
    MANAGE_STAFF = "manage_staff"
    VIEW_SERVICES = "view_services"
    MANAGE_SERVICES = "manage_services"
    VIEW_ALL_SCHEDULES = "view_all_schedules"
    MANAGE_ALL_SCHEDULES = "manage_all_schedules"
    VIEW_OWN_SCHEDULE = "view_own_schedule"
    MANAGE_OWN_SCHEDULE = "manage_own_schedule"
    VIEW_ALL_APPOINTMENTS = "view_all_appointments"
    MANAGE_ALL_APPOINTMENTS = "manage_all_appointments"
    VIEW_OWN_APPOINTMENTS = "view_own_appointments"
    MANAGE_OWN_APPOINTMENTS = "manage_own_appointments"
    VIEW_ALL_CLIENTS = "view_all_clients"
    MANAGE_ALL_CLIENTS = "manage_all_clients"
    VIEW_OWN_CLIENTS = "view_own_clients"
    MANAGE_OWN_CLIENTS = "manage_own_clients"
    VIEW_PAYMENTS = "view_payments"
    MANAGE_PAYMENTS = "manage_payments"
    REFUND_PAYMENTS = "refund_payments"
    VIEW_PREPAYMENTS = "view_prepayments"
    APPROVE_PREPAYMENTS = "approve_prepayments"
    REJECT_PREPAYMENTS = "reject_prepayments"
    EDIT_PAYMENT_INSTRUCTIONS = "edit_payment_instructions"
    EDIT_PAYMENT_TIMERS = "edit_payment_timers"
    CHANGE_PAYMENT_SETTINGS = "change_payment_settings"
    MANAGE_BROADCASTS = "manage_broadcasts"
    VIEW_ALL_STATISTICS = "view_all_statistics"
    VIEW_OWN_STATISTICS = "view_own_statistics"
    VIEW_FEATURE_FLAGS = "view_feature_flags"
    MANAGE_FEATURE_FLAGS = "manage_feature_flags"
    HANDLE_DATA_DELETION = "handle_data_deletion"
    VIEW_VENDOR_SUPPORT = "view_vendor_support"


_OWNER_PERMISSIONS = frozenset(StaffPermission)
_MANAGER_PERMISSIONS = frozenset(
    {
        StaffPermission.VIEW_BUSINESS,
        StaffPermission.VIEW_STAFF,
        StaffPermission.INVITE_STAFF,
        StaffPermission.MANAGE_STAFF,
        StaffPermission.VIEW_SERVICES,
        StaffPermission.MANAGE_SERVICES,
        StaffPermission.VIEW_ALL_SCHEDULES,
        StaffPermission.MANAGE_ALL_SCHEDULES,
        StaffPermission.VIEW_ALL_APPOINTMENTS,
        StaffPermission.MANAGE_ALL_APPOINTMENTS,
        StaffPermission.VIEW_ALL_CLIENTS,
        StaffPermission.MANAGE_ALL_CLIENTS,
        StaffPermission.VIEW_PAYMENTS,
        StaffPermission.MANAGE_PAYMENTS,
        StaffPermission.VIEW_PREPAYMENTS,
        StaffPermission.APPROVE_PREPAYMENTS,
        StaffPermission.REJECT_PREPAYMENTS,
        StaffPermission.EDIT_PAYMENT_INSTRUCTIONS,
        StaffPermission.EDIT_PAYMENT_TIMERS,
        StaffPermission.CHANGE_PAYMENT_SETTINGS,
        StaffPermission.MANAGE_BROADCASTS,
        StaffPermission.VIEW_ALL_STATISTICS,
        StaffPermission.VIEW_FEATURE_FLAGS,
        StaffPermission.HANDLE_DATA_DELETION,
        StaffPermission.VIEW_VENDOR_SUPPORT,
    }
)
_MASTER_PERMISSIONS = frozenset(
    {
        StaffPermission.VIEW_BUSINESS,
        StaffPermission.VIEW_SERVICES,
        StaffPermission.VIEW_OWN_SCHEDULE,
        StaffPermission.MANAGE_OWN_SCHEDULE,
        StaffPermission.VIEW_OWN_APPOINTMENTS,
        StaffPermission.MANAGE_OWN_APPOINTMENTS,
        StaffPermission.VIEW_OWN_CLIENTS,
        StaffPermission.MANAGE_OWN_CLIENTS,
        StaffPermission.VIEW_OWN_STATISTICS,
        StaffPermission.VIEW_VENDOR_SUPPORT,
    }
)
_RECEPTIONIST_PERMISSIONS = frozenset(
    {
        StaffPermission.VIEW_BUSINESS,
        StaffPermission.VIEW_STAFF,
        StaffPermission.VIEW_SERVICES,
        StaffPermission.VIEW_ALL_SCHEDULES,
        StaffPermission.VIEW_ALL_APPOINTMENTS,
        StaffPermission.MANAGE_ALL_APPOINTMENTS,
        StaffPermission.VIEW_ALL_CLIENTS,
        StaffPermission.MANAGE_ALL_CLIENTS,
        StaffPermission.VIEW_PAYMENTS,
        StaffPermission.VIEW_PREPAYMENTS,
        StaffPermission.APPROVE_PREPAYMENTS,
        StaffPermission.REJECT_PREPAYMENTS,
    }
)

ROLE_PERMISSIONS: Mapping[StaffRole, frozenset[StaffPermission]] = MappingProxyType(
    {
        StaffRole.OWNER: _OWNER_PERMISSIONS,
        StaffRole.MANAGER: _MANAGER_PERMISSIONS,
        StaffRole.MASTER: _MASTER_PERMISSIONS,
        StaffRole.RECEPTIONIST: _RECEPTIONIST_PERMISSIONS,
    }
)


def permissions_for_role(role: StaffRole) -> frozenset[StaffPermission]:
    """Return an immutable permission set for a persisted role."""

    return ROLE_PERMISSIONS[role]


def can_assign_role(actor_role: StaffRole, target_role: StaffRole) -> bool:
    """Prevent managers from creating peers or owners through an invitation."""

    if actor_role is StaffRole.OWNER:
        return True
    return actor_role is StaffRole.MANAGER and target_role in {
        StaffRole.MASTER,
        StaffRole.RECEPTIONIST,
    }


class StaffIdentity(BaseModel):
    """Telegram identity copied at bootstrap or invitation acceptance."""

    telegram_id: Annotated[int, Field(gt=0)]
    username: Annotated[str, Field(max_length=64)] | None = None
    first_name: Annotated[str, Field(max_length=255)] | None = None
    last_name: Annotated[str, Field(max_length=255)] | None = None


class StaffContext(BaseModel):
    """A server-derived, revocable authorization context for one business."""

    model_config = ConfigDict(frozen=True)

    business_id: Annotated[int, Field(gt=0)]
    staff_member_id: Annotated[int, Field(gt=0)]
    user_id: Annotated[int, Field(gt=0)]
    telegram_id: Annotated[int, Field(gt=0)]
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    role: StaffRole
    is_bookable: bool

    @property
    def permissions(self) -> frozenset[StaffPermission]:
        return permissions_for_role(self.role)

    def has_permission(self, permission: StaffPermission) -> bool:
        return permission in self.permissions


class StaffInvitationCreate(BaseModel):
    """Validated non-secret invitation attributes supplied by a staff member."""

    role: StaffRole
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    is_bookable: bool = False
    expires_in_hours: Annotated[int, Field(ge=1, le=24 * 30)] = 24

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def only_masters_are_bookable(self) -> StaffInvitationCreate:
        if self.is_bookable and self.role is not StaffRole.MASTER:
            raise ValueError("only a master invitation can be bookable")
        return self


class IssuedStaffInvitation(BaseModel):
    """The raw token is returned once and is redacted from repr/serialization."""

    invitation_id: Annotated[int, Field(gt=0)]
    business_id: Annotated[int, Field(gt=0)]
    role: StaffRole
    display_name: str
    is_bookable: bool
    expires_at: datetime
    token: SecretStr


class AcceptedStaffInvitation(BaseModel):
    """Safe result suitable for logging/audit without the invitation secret."""

    invitation_id: Annotated[int, Field(gt=0)]
    accepted_at: datetime
    staff: StaffContext


class RevokedStaffInvitation(BaseModel):
    invitation_id: Annotated[int, Field(gt=0)]
    business_id: Annotated[int, Field(gt=0)]
    role: StaffRole
    revoked_at: datetime


class StaffMemberView(BaseModel):
    """PII-minimal staff projection for the business administration screen."""

    id: Annotated[int, Field(gt=0)]
    display_name: str
    role: StaffRole
    is_active: bool
    is_bookable: bool
    is_bound: bool
    archived_at: datetime | None = None


class StaffInvitationView(BaseModel):
    """Pending invitation metadata; the one-time secret is intentionally absent."""

    id: Annotated[int, Field(gt=0)]
    role: StaffRole
    display_name: str
    is_bookable: bool
    expires_at: datetime


class StaffBootstrapResult(BaseModel):
    """Counts intentionally avoid putting configured Telegram IDs into audit metadata."""

    business_id: Annotated[int, Field(gt=0)]
    owner_already_present: bool
    created: tuple[StaffContext, ...] = ()
    skipped_existing_count: Annotated[int, Field(ge=0)] = 0
