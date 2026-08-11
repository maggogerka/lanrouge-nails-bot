# v0.4 release gates

CI installs both Python environments from hash-locked files. Runtime dependencies
come from `requirements-prod.lock`; test and security tools come from
`requirements.lock`. The local package is imported directly from the checked-out
working tree, so CI does not resolve unpinned project dependencies.

The workflow enforces:

- Ruff formatting/lint and strict mypy;
- a clean empty-database migration to Alembic head;
- the complete pytest suite with branch coverage and a 60% non-regression floor;
- a destructive, isolated seed of the v0.3.1 schema followed by upgrade to head
  and preservation/backfill assertions;
- Bandit medium/high-confidence application findings;
- `pip-audit` against the hash-locked production dependency graph;
- full-history Gitleaks scanning;
- runtime/test/backup image builds, Compose profile validation, backup-tool smoke,
  explicit disabled-backup behavior, and a HIGH/CRITICAL Trivy image scan.

The migration preservation test is skipped unless
`MIGRATION_PRESERVATION_TEST=1`. It additionally refuses to operate unless the
database name contains `test` or `migration`. Run it only against a fresh,
disposable database already migrated to `20260724_0010`:

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://app_user:password@localhost:5432/app_migration_test"
$env:DATABASE_URL = $env:TEST_DATABASE_URL
$env:MIGRATION_PRESERVATION_TEST = "1"
alembic upgrade 20260724_0010
pytest -q tests/ops/test_migration_v031_to_head.py
alembic check
```

Do not reuse the normal integration-test database for this check: it intentionally
expects an exact v0.3.1 revision and inserts fixed legacy IDs.

Gitleaks organization use can require a vendor license. `pip-audit` and Trivy need
access to current advisory databases, and the Docker job requires a Docker daemon.
Those are external CI prerequisites; the workflow intentionally does not turn a
missing scanner/database/license into a green result.
