"""Base domain exceptions mapped to friendly transport responses."""


class DomainError(Exception):
    """Expected business failure that must not be reported as a crash."""


class AuthorizationError(DomainError):
    """The actor is not allowed to execute an administrative use case."""


class EntityNotFoundError(DomainError):
    """A requested domain entity does not exist."""


class ServiceInUseError(DomainError):
    """A service with appointment history cannot be physically removed."""
