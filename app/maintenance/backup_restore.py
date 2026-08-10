"""Safe pg_dump/restic backup and explicitly guarded test-restore CLI core."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import IO, Protocol

from sqlalchemy.engine import URL, make_url

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"", "0", "false", "no", "off"}
_OFFSITE_REPOSITORY_PREFIXES = (
    "azure:",
    "b2:",
    "gs:",
    "rclone:",
    "rest:",
    "s3:",
    "sftp:",
)
_RESTORE_ACKNOWLEDGEMENT = "RESTORE_TO_SEPARATE_TEST_DATABASE"
_SAFE_DATABASE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_SYSTEM_ENVIRONMENT_KEYS = {
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}
_RESTIC_ENVIRONMENT_PREFIXES = (
    "AWS_",
    "AZURE_",
    "B2_",
    "GOOGLE_",
    "RCLONE_",
)
_RESTIC_ENVIRONMENT_KEYS = {
    "RESTIC_CACHE_DIR",
    "RESTIC_PASSWORD",
    "RESTIC_PASSWORD_FILE",
    "RESTIC_REPOSITORY",
}


class MaintenanceOperation(StrEnum):
    BACKUP = "backup"
    RESTORE_TEST = "restore_test"


class MaintenanceStatus(StrEnum):
    DISABLED = "disabled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MaintenanceHealth:
    operation: MaintenanceOperation
    status: MaintenanceStatus
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    error_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation.value,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "error_code": self.error_code,
        }


class MaintenanceFailureHook(Protocol):
    def __call__(self, health: MaintenanceHealth) -> None: ...


class BackupConfigurationError(RuntimeError):
    """Backup configuration is missing or could target unsafe storage/database state."""


@dataclass(frozen=True, slots=True, repr=False)
class BackupSettings:
    enabled: bool
    database_url: str = field(default="", repr=False)
    restore_database_url: str = field(default="", repr=False)
    restic_repository: str = field(default="", repr=False)
    restore_acknowledgement: str = field(default="", repr=False)
    keep_daily: int = 7
    keep_weekly: int = 5
    keep_monthly: int = 12
    command_timeout_seconds: int = 3_600
    process_environment: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return (
            "BackupSettings(enabled="
            f"{self.enabled}, keep_daily={self.keep_daily}, keep_weekly={self.keep_weekly}, "
            f"keep_monthly={self.keep_monthly}, command_timeout_seconds="
            f"{self.command_timeout_seconds}, secrets=<redacted>)"
        )

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> BackupSettings:
        source = dict(os.environ if environment is None else environment)
        enabled = _boolean(source.get("BACKUP_ENABLED", ""), name="BACKUP_ENABLED")
        if not enabled:
            return cls(enabled=False)
        return cls(
            enabled=True,
            database_url=source.get("DATABASE_URL", "").strip(),
            restore_database_url=source.get("RESTORE_DATABASE_URL", "").strip(),
            restic_repository=source.get("RESTIC_REPOSITORY", "").strip(),
            restore_acknowledgement=source.get("RESTORE_ACKNOWLEDGE", "").strip(),
            keep_daily=_bounded_integer(source, "BACKUP_KEEP_DAILY", default=7, maximum=365),
            keep_weekly=_bounded_integer(source, "BACKUP_KEEP_WEEKLY", default=5, maximum=104),
            keep_monthly=_bounded_integer(source, "BACKUP_KEEP_MONTHLY", default=12, maximum=120),
            command_timeout_seconds=_bounded_integer(
                source,
                "BACKUP_COMMAND_TIMEOUT_SECONDS",
                default=3_600,
                minimum=60,
                maximum=86_400,
            ),
            process_environment=source,
        )

    def validate_backup(self) -> DatabaseTarget:
        if not self.enabled:
            raise BackupConfigurationError("backup is disabled")
        if not self.database_url:
            raise BackupConfigurationError("DATABASE_URL is required")
        if not self.restic_repository:
            raise BackupConfigurationError("RESTIC_REPOSITORY is required")
        if not self.restic_repository.casefold().startswith(_OFFSITE_REPOSITORY_PREFIXES):
            raise BackupConfigurationError("RESTIC_REPOSITORY must be offsite")
        if not (
            self.process_environment.get("RESTIC_PASSWORD", "").strip()
            or self.process_environment.get("RESTIC_PASSWORD_FILE", "").strip()
        ):
            raise BackupConfigurationError("restic encryption password is required")
        return DatabaseTarget.from_url(self.database_url)

    def validate_restore(self) -> tuple[DatabaseTarget, DatabaseTarget]:
        source = self.validate_backup()
        if not self.restore_database_url:
            raise BackupConfigurationError("RESTORE_DATABASE_URL is required")
        if self.restore_acknowledgement != _RESTORE_ACKNOWLEDGEMENT:
            raise BackupConfigurationError("restore acknowledgement is required")
        target = DatabaseTarget.from_url(self.restore_database_url)
        if source.database.casefold() == target.database.casefold() or source.same_database(target):
            raise BackupConfigurationError("restore target must differ from production")
        normalized_name = target.database.casefold()
        if normalized_name in {"postgres", "template0", "template1"} or (
            "restore" not in normalized_name and "test" not in normalized_name
        ):
            raise BackupConfigurationError("restore target must be a test/restore database")
        return source, target


@dataclass(frozen=True, slots=True, repr=False)
class DatabaseTarget:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    sslmode: str | None = None

    @classmethod
    def from_url(cls, raw_url: str) -> DatabaseTarget:
        try:
            url: URL = make_url(raw_url)
        except Exception as exc:
            raise BackupConfigurationError("invalid PostgreSQL URL") from exc
        if not url.drivername.startswith("postgresql"):
            raise BackupConfigurationError("database URL must use PostgreSQL")
        database = (url.database or "").strip()
        username = (url.username or "").strip()
        if not database or not username or not _SAFE_DATABASE_NAME.fullmatch(database):
            raise BackupConfigurationError("database URL requires safe database/user values")
        query_sslmode = url.query.get("sslmode")
        sslmode = str(query_sslmode) if query_sslmode is not None else None
        return cls(
            host=url.host or "localhost",
            port=url.port or 5432,
            database=database,
            username=username,
            password=url.password or "",
            sslmode=sslmode,
        )

    def same_database(self, other: DatabaseTarget) -> bool:
        return (
            self.host.casefold(),
            self.port,
            self.database.casefold(),
        ) == (
            other.host.casefold(),
            other.port,
            other.database.casefold(),
        )


@dataclass(frozen=True, slots=True, repr=False)
class CommandSpec:
    argv: tuple[str, ...]
    environment: Mapping[str, str] = field(repr=False)
    timeout_seconds: int
    stdin_path: Path | None = None
    stdout_path: Path | None = None

    def __repr__(self) -> str:
        return (
            f"CommandSpec(executable={self.argv[0]!r}, argument_count={len(self.argv) - 1}, "
            f"environment_keys={sorted(self.environment)}, timeout_seconds="
            f"{self.timeout_seconds})"
        )


@dataclass(frozen=True, slots=True)
class CommandResult:
    return_code: int


class CommandRunner(Protocol):
    def __call__(self, command: CommandSpec) -> CommandResult: ...


class SubprocessCommandRunner:
    """Run fixed argv without a shell and discard potentially sensitive process output."""

    def __call__(self, command: CommandSpec) -> CommandResult:
        input_handle: IO[bytes] | None = None
        output_handle: IO[bytes] | None = None
        try:
            if command.stdin_path is not None:
                input_handle = command.stdin_path.open("rb")
            if command.stdout_path is not None:
                output_handle = command.stdout_path.open("wb")
            completed = subprocess.run(
                command.argv,
                check=False,
                shell=False,
                stdin=input_handle,
                stdout=output_handle or subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=dict(command.environment),
                timeout=command.timeout_seconds,
            )
            return CommandResult(completed.returncode)
        finally:
            if input_handle is not None:
                input_handle.close()
            if output_handle is not None:
                output_handle.close()


class _OperationFailure(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class BackupRestoreService:
    """Create encrypted offsite backups and restore only to a guarded test database."""

    def __init__(
        self,
        settings: BackupSettings,
        *,
        runner: CommandRunner | None = None,
        failure_hook: MaintenanceFailureHook | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner or SubprocessCommandRunner()
        self._failure_hook = failure_hook
        self._clock = clock or (lambda: datetime.now(UTC))

    def backup(self) -> MaintenanceHealth:
        started = self._now()
        if not self._settings.enabled:
            return self._health(
                MaintenanceOperation.BACKUP,
                MaintenanceStatus.DISABLED,
                started,
                "backup_disabled",
            )
        try:
            source = self._settings.validate_backup()
            with _secure_temporary_dump() as dump_path:
                self._pg_dump(source, dump_path)
                _require_nonempty_dump(dump_path, error_code="backup_dump_empty")
                self._restic_backup(dump_path)
            self._restic_retention()
        except BackupConfigurationError:
            return self._failed(
                MaintenanceOperation.BACKUP, started, "backup_configuration_invalid"
            )
        except _OperationFailure as exc:
            return self._failed(MaintenanceOperation.BACKUP, started, exc.error_code)
        except Exception:
            return self._failed(MaintenanceOperation.BACKUP, started, "backup_unexpected_error")
        return self._health(MaintenanceOperation.BACKUP, MaintenanceStatus.SUCCEEDED, started, None)

    def restore_test(self) -> MaintenanceHealth:
        started = self._now()
        if not self._settings.enabled:
            return self._health(
                MaintenanceOperation.RESTORE_TEST,
                MaintenanceStatus.DISABLED,
                started,
                "backup_disabled",
            )
        try:
            _, target = self._settings.validate_restore()
            with _secure_temporary_dump() as dump_path:
                self._restic_dump(dump_path)
                _require_nonempty_dump(dump_path, error_code="restore_dump_empty")
                self._validate_dump(dump_path)
                self._pg_restore(target, dump_path)
                self._verify_restore(target)
        except BackupConfigurationError:
            return self._failed(
                MaintenanceOperation.RESTORE_TEST,
                started,
                "restore_configuration_invalid",
            )
        except _OperationFailure as exc:
            return self._failed(MaintenanceOperation.RESTORE_TEST, started, exc.error_code)
        except Exception:
            return self._failed(
                MaintenanceOperation.RESTORE_TEST, started, "restore_unexpected_error"
            )
        return self._health(
            MaintenanceOperation.RESTORE_TEST,
            MaintenanceStatus.SUCCEEDED,
            started,
            None,
        )

    def _pg_dump(self, target: DatabaseTarget, dump_path: Path) -> None:
        self._run(
            CommandSpec(
                argv=(
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--no-password",
                    "--file",
                    str(dump_path),
                ),
                environment=_postgres_environment(self._settings.process_environment, target),
                timeout_seconds=self._settings.command_timeout_seconds,
            ),
            "pg_dump_failed",
        )

    def _restic_backup(self, dump_path: Path) -> None:
        self._run(
            CommandSpec(
                argv=(
                    "restic",
                    "backup",
                    "--stdin",
                    "--stdin-filename",
                    "lanrouge-postgres.dump",
                    "--tag",
                    "lanrouge-postgres",
                ),
                environment=_restic_environment(
                    self._settings.process_environment,
                    repository=self._settings.restic_repository,
                ),
                timeout_seconds=self._settings.command_timeout_seconds,
                stdin_path=dump_path,
            ),
            "restic_backup_failed",
        )

    def _restic_retention(self) -> None:
        self._run(
            CommandSpec(
                argv=(
                    "restic",
                    "forget",
                    "--tag",
                    "lanrouge-postgres",
                    "--keep-daily",
                    str(self._settings.keep_daily),
                    "--keep-weekly",
                    str(self._settings.keep_weekly),
                    "--keep-monthly",
                    str(self._settings.keep_monthly),
                    "--prune",
                ),
                environment=_restic_environment(
                    self._settings.process_environment,
                    repository=self._settings.restic_repository,
                ),
                timeout_seconds=self._settings.command_timeout_seconds,
            ),
            "restic_retention_failed",
        )

    def _restic_dump(self, dump_path: Path) -> None:
        self._run(
            CommandSpec(
                argv=(
                    "restic",
                    "dump",
                    "--tag",
                    "lanrouge-postgres",
                    "latest",
                    "lanrouge-postgres.dump",
                ),
                environment=_restic_environment(
                    self._settings.process_environment,
                    repository=self._settings.restic_repository,
                ),
                timeout_seconds=self._settings.command_timeout_seconds,
                stdout_path=dump_path,
            ),
            "restic_restore_download_failed",
        )

    def _validate_dump(self, dump_path: Path) -> None:
        self._run(
            CommandSpec(
                argv=("pg_restore", "--list", str(dump_path)),
                environment=_system_environment(self._settings.process_environment),
                timeout_seconds=self._settings.command_timeout_seconds,
            ),
            "pg_restore_validation_failed",
        )

    def _pg_restore(self, target: DatabaseTarget, dump_path: Path) -> None:
        self._run(
            CommandSpec(
                argv=(
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    "--no-password",
                    "--dbname",
                    target.database,
                    str(dump_path),
                ),
                environment=_postgres_environment(self._settings.process_environment, target),
                timeout_seconds=self._settings.command_timeout_seconds,
            ),
            "pg_restore_failed",
        )

    def _verify_restore(self, target: DatabaseTarget) -> None:
        self._run(
            CommandSpec(
                argv=(
                    "psql",
                    "--no-password",
                    "--no-psqlrc",
                    "--set",
                    "ON_ERROR_STOP=1",
                    "--dbname",
                    target.database,
                    "--command",
                    "SELECT 1",
                ),
                environment=_postgres_environment(self._settings.process_environment, target),
                timeout_seconds=min(self._settings.command_timeout_seconds, 300),
            ),
            "restore_verification_failed",
        )

    def _run(self, command: CommandSpec, error_code: str) -> None:
        try:
            result = self._runner(command)
        except Exception as exc:
            raise _OperationFailure(error_code) from exc
        if result.return_code != 0:
            raise _OperationFailure(error_code)

    def _failed(
        self,
        operation: MaintenanceOperation,
        started: datetime,
        error_code: str,
    ) -> MaintenanceHealth:
        health = self._health(operation, MaintenanceStatus.FAILED, started, error_code)
        if self._failure_hook is not None:
            try:
                self._failure_hook(health)
            except Exception:
                pass
        return health

    def _health(
        self,
        operation: MaintenanceOperation,
        status: MaintenanceStatus,
        started: datetime,
        error_code: str | None,
    ) -> MaintenanceHealth:
        finished = self._now()
        return MaintenanceHealth(
            operation=operation,
            status=status,
            started_at=started,
            finished_at=finished,
            duration_seconds=max(0.0, (finished - started).total_seconds()),
            error_code=error_code,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("maintenance clock must be timezone-aware")
        return value.astimezone(UTC)


@contextmanager
def _secure_temporary_dump() -> Iterator[Path]:
    descriptor, raw_path = tempfile.mkstemp(prefix="lanrouge-backup-", suffix=".dump")
    path = Path(raw_path)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        os.close(descriptor)
        descriptor = -1
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise PermissionError("temporary backup permissions are not 0600")
        yield path
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)


def _require_nonempty_dump(path: Path, *, error_code: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise _OperationFailure(error_code)


def _system_environment(source: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in source.items() if key in _SYSTEM_ENVIRONMENT_KEYS}


def _postgres_environment(source: Mapping[str, str], target: DatabaseTarget) -> dict[str, str]:
    environment = _system_environment(source)
    environment.update(
        {
            "PGHOST": target.host,
            "PGPORT": str(target.port),
            "PGDATABASE": target.database,
            "PGUSER": target.username,
            "PGPASSWORD": target.password,
        }
    )
    if target.sslmode is not None:
        environment["PGSSLMODE"] = target.sslmode
    return environment


def _restic_environment(source: Mapping[str, str], *, repository: str) -> dict[str, str]:
    environment = _system_environment(source)
    environment.update(
        {
            key: value
            for key, value in source.items()
            if key in _RESTIC_ENVIRONMENT_KEYS or key.startswith(_RESTIC_ENVIRONMENT_PREFIXES)
        }
    )
    environment["RESTIC_REPOSITORY"] = repository
    return environment


def _boolean(raw: str, *, name: str) -> bool:
    normalized = raw.strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise BackupConfigurationError(f"{name} must be a boolean")


def _bounded_integer(
    source: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int = 1,
    maximum: int,
) -> int:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise BackupConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise BackupConfigurationError(f"{name} is outside the safe range")
    return value


def _configuration_failure(operation: MaintenanceOperation) -> MaintenanceHealth:
    now = datetime.now(UTC)
    return MaintenanceHealth(
        operation=operation,
        status=MaintenanceStatus.FAILED,
        started_at=now,
        finished_at=now,
        duration_seconds=0.0,
        error_code=(
            "backup_configuration_invalid"
            if operation is MaintenanceOperation.BACKUP
            else "restore_configuration_invalid"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encrypted PostgreSQL backup operations")
    parser.add_argument("operation", choices=("backup", "restore-test"))
    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    operation = (
        MaintenanceOperation.BACKUP
        if args.operation == "backup"
        else MaintenanceOperation.RESTORE_TEST
    )
    try:
        settings = BackupSettings.from_environment(environment)
    except BackupConfigurationError:
        result = _configuration_failure(operation)
    else:
        service = BackupRestoreService(settings)
        result = (
            service.backup() if operation is MaintenanceOperation.BACKUP else service.restore_test()
        )
    print(json.dumps(result.as_dict(), ensure_ascii=False, separators=(",", ":")))
    if result.status in {MaintenanceStatus.SUCCEEDED, MaintenanceStatus.DISABLED}:
        return 0
    return 2 if result.error_code and "configuration" in result.error_code else 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
