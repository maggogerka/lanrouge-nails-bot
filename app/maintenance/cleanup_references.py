"""Dry-run or execute appointment-reference retention cleanup."""

from __future__ import annotations

import argparse
import asyncio
import json

from app.config import RuntimeConfigurationError, get_settings
from app.database import Database
from app.repositories import SqlAlchemyUnitOfWork
from app.services.reference_cleanup_service import ReferenceCleanupService


async def run_command(*, dry_run: bool) -> int:
    settings = get_settings()
    settings.validate_database_runtime()
    database = Database.create(settings.database_url.get_secret_value())
    try:
        service = ReferenceCleanupService(lambda: SqlAlchemyUnitOfWork(database.sessions))
        result = await service.run(dry_run=dry_run)
    finally:
        await database.close()
    print(
        json.dumps(
            {
                "checked": result.checked,
                "deleted": result.deleted,
                "estimated_bytes_released": result.estimated_bytes_released,
                "errors": result.errors,
                "duration_seconds": round(result.duration_seconds, 3),
                "dry_run": result.dry_run,
            },
            ensure_ascii=False,
        )
    )
    return 1 if result.errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean expired Telegram reference metadata")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="report candidates only")
    mode.add_argument("--execute", action="store_true", help="anonymize expired identifiers")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(run_command(dry_run=bool(args.dry_run))))
    except RuntimeConfigurationError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
