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


class PrivacyConsentRequiredError(DomainError):
    """A client action requires explicit privacy consent."""


class BookingUnavailableError(DomainError):
    """The selected service or window cannot currently be booked."""


class BookingConflictError(BookingUnavailableError):
    """A concurrent client has already occupied the selected window."""


class BookingLimitError(BookingUnavailableError):
    """The business-day appointment capacity has been exhausted."""


class PortfolioStateError(DomainError):
    """A portfolio work cannot perform the requested lifecycle transition."""


class AppointmentNotFoundError(DomainError):
    """An appointment is absent or not visible to the current client."""


class AppointmentStateError(DomainError):
    """An appointment transition is invalid for its current state."""


class CancellationDeadlineError(DomainError):
    """A client-initiated change is too close to the appointment start."""
