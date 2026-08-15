"""Deployment contracts for persistent backup and freshness timers."""

from pathlib import Path

import pytest

SYSTEMD_DIR = Path("deploy/systemd")


@pytest.mark.parametrize(
    "name",
    [
        "telegram-crm-backup.timer",
        "telegram-crm-backup-freshness.timer",
        "telegram-crm-backup-restore-test.timer",
    ],
)
def test_backup_timers_are_persistent_after_vps_reboot(name: str) -> None:
    text = (SYSTEMD_DIR / name).read_text(encoding="utf-8")

    assert "Persistent=true" in text
    assert "WantedBy=timers.target" in text


def test_backup_services_use_compose_without_secret_arguments() -> None:
    services = [
        (SYSTEMD_DIR / name).read_text(encoding="utf-8")
        for name in (
            "telegram-crm-backup.service",
            "telegram-crm-backup-freshness.service",
            "telegram-crm-backup-restore-test.service",
        )
    ]
    backup, freshness, restore = services

    assert "--profile backup run --rm backup" in backup
    assert "check-freshness" in freshness
    assert "restore-test" in restore
    assert all("--require-enabled" in text for text in services)
    assert all("--project-name ${COMPOSE_PROJECT_NAME}" in text for text in services)
    assert all("--env-file ${ENV_FILE}" in text for text in services)
    assert all("EnvironmentFile=/etc/telegram-crm/backup.env" in text for text in services)
    assert all("OnFailure=telegram-crm-backup-alert@%n.service" in text for text in services)
    commands = "\n".join(
        line for text in services for line in text.splitlines() if line.startswith("Exec")
    )
    assert "PASSWORD=" not in commands
    assert "SECRET=" not in commands


def test_systemd_environment_template_binds_one_explicit_compose_project() -> None:
    text = (SYSTEMD_DIR / "backup.env.example").read_text(encoding="utf-8")

    assert "COMPOSE_PROJECT_NAME=telegram-crm-production" in text
    assert "ENV_FILE=/opt/telegram-crm-bot/.env" in text
    assert "POSTGRES_PASSWORD_SECRET_FILE=/etc/telegram-crm/secrets/postgres_password" in text
    assert (
        "RESTORE_POSTGRES_PASSWORD_SECRET_FILE="
        "/etc/telegram-crm/secrets/restore_postgres_password" in text
    )
    assert "RESTIC_PASSWORD_SECRET_FILE=/etc/telegram-crm/secrets/restic_password" in text


def test_backup_compose_uses_a_separate_restore_password_secret() -> None:
    profile = Path("compose.profiles.yml").read_text(encoding="utf-8")
    ci_override = Path("compose.ci.yml").read_text(encoding="utf-8")

    assert "RESTORE_DATABASE_PASSWORD_FILE: /run/secrets/restore_postgres_password" in profile
    assert "- restore_postgres_password" in profile
    assert (
        "${RESTORE_POSTGRES_PASSWORD_SECRET_FILE:-./.secrets/restore_postgres_password}" in profile
    )
    assert "RESTORE_DATABASE_PASSWORD_FILE: /run/secrets/postgres_password" not in ci_override
    assert "postgresql+asyncpg://restore_user:placeholder@postgres" in ci_override
