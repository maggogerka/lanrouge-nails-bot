"""Base domain exceptions mapped to friendly transport responses."""


class DomainError(Exception):
    """Expected business failure that must not be reported as a crash."""


class AuthorizationError(DomainError):
    """The actor is not allowed to execute an administrative use case."""


class EntityNotFoundError(DomainError):
    """A requested domain entity does not exist."""


class ServiceInUseError(DomainError):
    """A service with appointment history cannot be physically removed."""


class WindowValidationError(DomainError):
    """A requested window violates a calendar or spacing rule."""


class WindowStateError(DomainError):
    """An operation is not valid for the current window status."""


class WindowInUseError(DomainError):
    """A window with booking history cannot be physically removed."""
