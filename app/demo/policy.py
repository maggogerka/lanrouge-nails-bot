"""Single fail-closed policy for every public-demo capability."""

from __future__ import annotations

from enum import StrEnum


class DemoOperation(StrEnum):
    READ = "read"
    BOOK = "book"
    UPDATE_APPOINTMENT = "update_appointment"
    ADD_SERVICE = "add_service"
    ADD_WINDOW = "add_window"
    RESET = "reset"
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
    """Raised when a production side effect is requested by demo code."""


class DemoPolicy:
    """Allow only mutations contained inside the caller's demo workspace."""

    _ALLOWED = frozenset(
        {
            DemoOperation.READ,
            DemoOperation.BOOK,
            DemoOperation.UPDATE_APPOINTMENT,
            DemoOperation.ADD_SERVICE,
            DemoOperation.ADD_WINDOW,
            DemoOperation.RESET,
        }
    )

    def require(self, operation: DemoOperation) -> None:
        if operation not in self._ALLOWED:
            raise DemoActionBlocked(
                "Это демонстрация функции. В рабочем боте действие выполняется после "
                "явного подтверждения владельца; здесь внешние запросы не отправляются."
            )

    def is_allowed(self, operation: DemoOperation) -> bool:
        return operation in self._ALLOWED
