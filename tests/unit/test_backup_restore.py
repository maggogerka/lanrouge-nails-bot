"""Backup encryption pipeline, retention and destructive restore guards."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from app.maintenance.backup_restore import (
    BackupConfigurationError,
    BackupRestoreService,
    BackupSettings,
    CommandResult,
    CommandSpec,
    MaintenanceHealth,
    MaintenanceStatus,
    run_cli,
)


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "BACKUP_ENABLED": "true",
        "DATABASE_URL": ("postgresql+asyncpg://app_user:production-password@db:5432/app_db"),
        "RESTIC_REPOSITORY": "s3:s3.example.test/telegram-crm-backups",
        "RESTIC_PASSWORD": "restic-password",
        "AWS_ACCESS_KEY_ID": "access-key",
        "AWS_SECRET_ACCESS_KEY": "object-storage-secret",
        "BOT_TOKEN": "telegram-secret",
        "PATH": "/usr/local/bin:/usr/bin",
    }
    values.update(overrides)
    return values


class FakeRunner:
    def __init__(self, *, fail_at: tuple[str, str] | None = None) -> None:
        self.calls: list[CommandSpec] = []
        self.fail_at = fail_at
        self.dump_mode: int | None = None
        self.dump_path: Path | None = None

    def __call__(self, command: CommandSpec) -> CommandResult:
        self.calls.append(command)
        marker = (command.argv[0], command.argv[1] if len(command.argv) > 1 else "")
        if marker == self.fail_at:
            return CommandResult(1)
        if marker == ("pg_dump", "--format=custom"):
            path = Path(command.argv[command.argv.index("--file") + 1])
            self.dump_path = path
            self.dump_mode = stat.S_IMODE(path.stat().st_mode)
            path.write_bytes(b"PGDMP-safe-test")
        if marker == ("restic", "dump"):
            assert command.stdout_path is not None
            self.dump_path = command.stdout_path
            self.dump_mode = stat.S_IMODE(command.stdout_path.stat().st_mode)
            command.stdout_path.write_bytes(b"PGDMP-restored-test")
        return CommandResult(0)


def test_missing_configuration_is_explicitly_disabled(capsys: object) -> None:
    runner = FakeRunner()
    settings = BackupSettings.from_environment({})

    result = BackupRestoreService(settings, runner=runner).backup()

    assert result.status is MaintenanceStatus.DISABLED
    assert result.error_code == "backup_disabled"
    assert runner.calls == []
    assert run_cli(["backup"], environment={}) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert json.loads(output)["status"] == "disabled"


def test_backup_uses_custom_0600_temp_streams_to_restic_and_applies_retention() -> None:
    runner = FakeRunner()
    settings = BackupSettings.from_environment(environment())

    result = BackupRestoreService(settings, runner=runner).backup()

    assert result.status is MaintenanceStatus.SUCCEEDED
    assert [call.argv[:2] for call in runner.calls] == [
        ("pg_dump", "--format=custom"),
        ("restic", "backup"),
        ("restic", "forget"),
    ]
    assert runner.dump_mode is not None
    if os.name != "nt":
        assert runner.dump_mode == 0o600
    assert runner.dump_path is not None and not runner.dump_path.exists()
    dump, backup, retention = runner.calls
    assert "--no-password" in dump.argv
    assert backup.stdin_path == runner.dump_path
    assert "--stdin" in backup.argv
    assert "--keep-daily" in retention.argv
    assert "--keep-weekly" in retention.argv
    assert "--keep-monthly" in retention.argv
    assert dump.environment["PGPASSWORD"] == "production-password"
    assert "DATABASE_URL" not in dump.environment
    assert "BOT_TOKEN" not in dump.environment
    assert "RESTIC_PASSWORD" not in dump.environment
    assert backup.environment["RESTIC_PASSWORD"] == "restic-password"
    assert "DATABASE_URL" not in backup.environment
    assert "BOT_TOKEN" not in backup.environment
    assert "PGPASSWORD" not in backup.environment
    assert "production-password" not in repr(settings)
    assert "restic-password" not in repr(settings)


def test_local_unencrypted_repository_is_rejected_and_failure_hook_receives_safe_health() -> None:
    runner = FakeRunner()
    failures: list[MaintenanceHealth] = []
    settings = BackupSettings.from_environment(environment(RESTIC_REPOSITORY="/var/backups"))

    result = BackupRestoreService(settings, runner=runner, failure_hook=failures.append).backup()

    assert result.status is MaintenanceStatus.FAILED
    assert result.error_code == "backup_configuration_invalid"
    assert failures == [result]
    assert runner.calls == []
    serialized = json.dumps(result.as_dict())
    assert "production-password" not in serialized
    assert "/var/backups" not in serialized


def test_connection_password_files_replace_placeholders_without_leaking(
    tmp_path: Path,
) -> None:
    database_password = tmp_path / "database-password"
    restore_password = tmp_path / "restore-password"
    database_password.write_text("source:@/secret", encoding="utf-8")
    restore_password.write_text("restore:@/secret", encoding="utf-8")

    settings = BackupSettings.from_environment(
        environment(
            DATABASE_URL="postgresql+asyncpg://app_user:placeholder@db:5432/app_db",
            DATABASE_PASSWORD_FILE=str(database_password),
            RESTORE_DATABASE_URL=(
                "postgresql+asyncpg://restore_user:placeholder@db:5432/app_restore_test"
            ),
            RESTORE_DATABASE_PASSWORD_FILE=str(restore_password),
            RESTORE_ACKNOWLEDGE="RESTORE_TO_SEPARATE_TEST_DATABASE",
        )
    )

    source, target = settings.validate_restore()

    assert source.password == "source:@/secret"
    assert target.password == "restore:@/secret"
    assert "source:@/secret" not in repr(settings)
    assert "restore:@/secret" not in repr(settings)


def test_url_file_conflict_and_non_test_local_repository_are_rejected(tmp_path: Path) -> None:
    database_url = tmp_path / "database-url"
    database_url.write_text(
        "postgresql+asyncpg://app_user:file-secret@db:5432/app_db",
        encoding="utf-8",
    )

    with pytest.raises(BackupConfigurationError, match="mutually exclusive"):
        BackupSettings.from_environment(environment(DATABASE_URL_FILE=str(database_url)))

    with pytest.raises(BackupConfigurationError, match="APP_ENV=test"):
        BackupSettings.from_environment(
            environment(
                RESTIC_REPOSITORY="/test/repository",
                BACKUP_ALLOW_LOCAL_REPOSITORY_FOR_TESTS="true",
                APP_ENV="production",
            )
        )


def test_local_repository_requires_explicit_test_environment() -> None:
    settings = BackupSettings.from_environment(
        environment(
            RESTIC_REPOSITORY="/test/repository",
            BACKUP_ALLOW_LOCAL_REPOSITORY_FOR_TESTS="true",
            APP_ENV="test",
        )
    )

    assert settings.validate_backup().database == "app_db"


def test_command_failure_has_stable_code_and_never_exposes_process_details() -> None:
    runner = FakeRunner(fail_at=("restic", "backup"))
    settings = BackupSettings.from_environment(environment())

    result = BackupRestoreService(settings, runner=runner).backup()

    assert result.status is MaintenanceStatus.FAILED
    assert result.error_code == "restic_backup_failed"
    assert runner.dump_path is not None and not runner.dump_path.exists()
    assert "password" not in json.dumps(result.as_dict())


def test_restore_rejects_production_or_unguarded_target_before_any_command() -> None:
    runner = FakeRunner()
    same_database = BackupSettings.from_environment(
        environment(
            RESTORE_DATABASE_URL=("postgresql+asyncpg://restore:restore-password@db:5432/app_db"),
            RESTORE_ACKNOWLEDGE="RESTORE_TO_SEPARATE_TEST_DATABASE",
        )
    )
    unacknowledged = BackupSettings.from_environment(
        environment(
            RESTORE_DATABASE_URL=(
                "postgresql+asyncpg://restore:restore-password@db:5432/app_restore"
            )
        )
    )

    first = BackupRestoreService(same_database, runner=runner).restore_test()
    second = BackupRestoreService(unacknowledged, runner=runner).restore_test()

    assert first.error_code == "restore_configuration_invalid"
    assert second.error_code == "restore_configuration_invalid"
    assert runner.calls == []


def test_guarded_restore_downloads_validates_restores_and_verifies_separate_database() -> None:
    runner = FakeRunner()
    settings = BackupSettings.from_environment(
        environment(
            RESTORE_DATABASE_URL=(
                "postgresql+asyncpg://restore_user:restore-password@db:5432/app_restore_test"
            ),
            RESTORE_ACKNOWLEDGE="RESTORE_TO_SEPARATE_TEST_DATABASE",
        )
    )

    result = BackupRestoreService(settings, runner=runner).restore_test()

    assert result.status is MaintenanceStatus.SUCCEEDED
    assert [call.argv[:2] for call in runner.calls] == [
        ("restic", "dump"),
        ("pg_restore", "--list"),
        ("pg_restore", "--clean"),
        ("psql", "--no-password"),
    ]
    assert runner.dump_mode is not None
    if os.name != "nt":
        assert runner.dump_mode == 0o600
    assert runner.dump_path is not None and not runner.dump_path.exists()
    restore = runner.calls[2]
    assert "--clean" in restore.argv
    assert "--if-exists" in restore.argv
    assert "--exit-on-error" in restore.argv
    assert restore.environment["PGDATABASE"] == "app_restore_test"
    assert restore.environment["PGPASSWORD"] == "restore-password"
    all_arguments = " ".join(item for call in runner.calls for item in call.argv)
    assert "production-password" not in all_arguments
    assert "restore-password" not in all_arguments
