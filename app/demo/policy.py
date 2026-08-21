"""Central fail-closed capability policy for the public demo."""

from __future__ import annotations

from enum import StrEnum


class DemoOperation(StrEnum):
    READ = "read"
    NAVIGATE = "navigate"
    TRANSIENT_STATE = "transient_state"
    CREATE_APPOINTMENT = "create_appointment"
    UPDATE_APPOINTMENT = "update_appointment"
    CREATE_WAITLIST_ENTRY = "create_waitlist_entry"
    CREATE_REVIEW = "create_review"
    ADD_SERVICE = "add_service"
    ADD_WINDOW = "add_window"
    CHANGE_SETTINGS = "change_settings"
    CHANGE_PAYMENT = "change_payment"
    PAYMENT = "payment"
    REFUND = "refund"
    BROADCAST = "broadcast"
    EXTERNAL_NOTIFICATION = "external_notification"
    STAFF_INVITATION = "staff_invitation"
    OWNER_BOOTSTRAP = "owner_bootstrap"
    BACKUP = "backup"
    PERSONAL_DATA_EXPORT = "personal_data_export"
    FILE_UPLOAD = "file_upload"
    PRODUCTION_API = "production_api"


class DemoActionBlocked(PermissionError):
    """Raised before a demonstration can perform a business side effect."""


class DemoPolicy:
    """Only read-only navigation and short-lived FSM state are permitted."""

    _ALLOWED = frozenset(
        {DemoOperation.READ, DemoOperation.NAVIGATE, DemoOperation.TRANSIENT_STATE}
    )

    def require(self, operation: DemoOperation) -> None:
        if operation not in self._ALLOWED:
            raise DemoActionBlocked(
                "Это действие показано полностью, но сохранение доступно только в рабочей "
                "версии после покупки. Демо ничего не записало, не отправило и не изменило."
            )

    def is_allowed(self, operation: DemoOperation) -> bool:
        return operation in self._ALLOWED
