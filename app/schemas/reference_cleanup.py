"""Non-sensitive cleanup summaries for CLI, workers and tests."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ReferenceCleanupResult:
    checked: int
    deleted: int
    estimated_bytes_released: int
    errors: int
    duration_seconds: float
    dry_run: bool


@dataclass(frozen=True, slots=True)
class ReferenceCleanupHealth:
    last_started_at: datetime | None
    last_succeeded_at: datetime | None
    consecutive_failures: int
    last_error_code: str | None
