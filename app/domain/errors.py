"""Base domain exceptions mapped to friendly transport responses."""


class DomainError(Exception):
    """Expected business failure that must not be reported as a crash."""
