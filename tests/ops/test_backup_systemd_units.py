"""Deployment contracts for persistent backup and freshness timers."""

from pathlib import Path

import pytest

SYSTEMD_DIR = Path("deploy/systemd")


@pytest.mark.parametrize(
    "name",
    ["lanrouge-backup.timer", "lanrouge-backup-freshness.timer"],
)
def test_backup_timers_are_persistent_after_vps_reboot(name: str) -> None:
    text = (SYSTEMD_DIR / name).read_text(encoding="utf-8")

    assert "Persistent=true" in text
    assert "WantedBy=timers.target" in text


def test_backup_services_use_compose_without_secret_arguments() -> None:
    backup = (SYSTEMD_DIR / "lanrouge-backup.service").read_text(encoding="utf-8")
    freshness = (SYSTEMD_DIR / "lanrouge-backup-freshness.service").read_text(encoding="utf-8")

    assert "--profile backup run --rm backup" in backup
    assert "check-freshness" in freshness
    commands = "\n".join(
        line
        for text in (backup, freshness)
        for line in text.splitlines()
        if line.startswith("Exec")
    )
    assert "PASSWORD=" not in commands
    assert "SECRET=" not in commands
