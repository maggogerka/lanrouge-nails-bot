# Production Docker deployment (v0.4)

This document covers the hardened Compose boundary. It does not provision a VPS,
TLS, a secret manager, object storage, or monitoring infrastructure.

## Required secret and environment boundary

Create secret files outside source control before the first start:

```text
.secrets/postgres_password
.secrets/redis_password
.secrets/restic_password    # only when the backup profile is used
```

The Redis password must be at least 32 base64url-safe characters (`A-Z`, `a-z`,
`0-9`, `_`, `-`). Files may live elsewhere by setting these host-side variables:

- `POSTGRES_PASSWORD_SECRET_FILE`
- `REDIS_PASSWORD_SECRET_FILE`
- `RESTIC_PASSWORD_SECRET_FILE`
- `YOOKASSA_SHOP_ID_SECRET_FILE`
- `YOOKASSA_SECRET_KEY_SECRET_FILE`

For Compose file-backed secrets used by non-root containers, keep the containing
directory mode `0700` and the individual files mode `0644`; the protected parent
prevents other host users from traversing to the files, while the read-only bind
mount remains readable inside the container. Docker mounts them at
`/run/secrets/...`; PostgreSQL consumes `POSTGRES_PASSWORD_FILE`, Redis creates a
mode-0600 configuration file in its tmpfs, and restic consumes
`RESTIC_PASSWORD_FILE`. A production secret-manager integration may enforce an
equivalent UID/GID-specific policy instead.

The deployment environment file selected through `ENV_FILE` must provide
`DATABASE_URL` and `REDIS_URL` with their normal username, host, port and database
components. Compose mounts the PostgreSQL and Redis password files into every
application container; `Settings` safely replaces the password components at
runtime, including URL-encoding. This makes the password files authoritative and
avoids duplicated credentials. A non-Compose deployment may instead provide full
URLs directly or use the matching application `*_FILE` settings. The environment
file must also contain the normal runtime values such as
`BOT_TOKEN`, `ADMIN_TELEGRAM_IDS`, `PRIVACY_POLICY_URL`, and optional
`SENTRY_DSN`. Do not put that file in the image or repository.

Set `REDIS_NAMESPACE` in the shell or Compose `.env` file (default `telegram_crm`).
The variable is preserved through the Compose environment anchors and is wired
into every application container, including the permanent reservation worker and
the optional API. Redis-backed sessions, limits, locks, and heartbeats use this namespace.

Compose no longer forces a global project name. Set a unique name per deployment:

```powershell
$env:COMPOSE_PROJECT_NAME = "telegram-crm-production"
$env:ENV_FILE = "C:\secure\telegram-crm.env"
$env:POSTGRES_PASSWORD_SECRET_FILE = "C:\secure\postgres_password"
$env:REDIS_PASSWORD_SECRET_FILE = "C:\secure\redis_password"
```

## Validate and start

```powershell
docker compose -f docker-compose.yml -f compose.production.yml config --quiet
docker compose -f docker-compose.yml -f compose.production.yml -f compose.profiles.yml --profile api --profile backup config --quiet
docker compose -f docker-compose.yml -f compose.production.yml build
docker compose -f docker-compose.yml -f compose.production.yml up -d
docker compose -f docker-compose.yml -f compose.production.yml ps
docker compose -f docker-compose.yml -f compose.production.yml run --rm bot python -m app.healthcheck --component bot
```

`postgres` and `redis` exist only on the internal backend network and publish no
host ports. Application containers also join an egress network for Telegram,
payment providers, Sentry, and offsite backup services. They run as UID/GID 10001,
with a read-only root filesystem, a small tmpfs, all Linux capabilities dropped,
`no-new-privileges`, PID/CPU/memory limits, and bounded JSON log files. Stateful
resource limits are in `compose.production.yml` and can be tuned using its named
environment variables.

The PostgreSQL container intentionally keeps the official image's initialization
user/capability behavior: first boot must initialize and set ownership on the
named volume. Do not claim `cap_drop: ALL` or a read-only root for that phase
without first provisioning the volume and validating upgrades on the target host.
Database isolation here comes from the internal network, absent published ports,
the password file, `no-new-privileges`, and host-level Docker access controls.

Each permanent service selects its own heartbeat through
`HEALTHCHECK_COMPONENT`: `bot`, `reminders`, `broadcasts`, `reference_cleanup`,
`privacy_deletion`, or `reservation_expiry`. The image healthcheck verifies PostgreSQL,
Redis, and only that service's heartbeat, so a stopped process does not mark unrelated
containers unhealthy. Process exit remains visible through the container state.

## Optional profiles

Encrypted offsite backup is a one-shot job:

```powershell
docker compose -f docker-compose.yml -f compose.production.yml -f compose.profiles.yml --profile backup run --rm backup
```

Set `BACKUP_ENABLED=true`, `RESTIC_REPOSITORY` to a supported offsite repository,
the provider credentials, and the restic password secret. Compose also mounts
`postgres_password`; backup replaces the placeholder password in `DATABASE_URL`
using `DATABASE_PASSWORD_FILE` without exporting the resulting URL. The dump lives in
a mode-0600 temporary file on a configurable tmpfs (`BACKUP_TMPFS_SIZE`, default
`1g`). Size this above the largest custom-format dump. Retention defaults are
implemented by the backup core. Restore remains a separate, guarded
`restore-test` operation and must target a different database whose name contains
`test` or `restore`; see `backup-restore.md`.

The HTTP API is optional. The reservation expiry worker is a permanent base
service because payment reservations must expire even without Mini App. Validate
and start the API with:

```powershell
docker compose -f docker-compose.yml -f compose.production.yml -f compose.profiles.yml --profile api config --quiet
docker compose -f docker-compose.yml -f compose.production.yml -f compose.profiles.yml --profile api up -d api
docker compose -f docker-compose.yml -f compose.production.yml -f compose.profiles.yml --profile api ps api reservation-worker
```

The API runs `python -m app.api`, binds to internal port `8080`, and publishes no
host port. Its healthcheck requests `GET /health/ready` on loopback and uses the
first exact value from `API_ALLOWED_HOSTS` as the `Host` header. Configure at
least one safe host plus `MINI_APP_ALLOWED_ORIGINS` and the API signing/rate-limit
keys before enabling the profile. YooKassa is optional; partial provider configuration is
rejected. A separately
managed reverse proxy must join an appropriate Docker network if external API
access is later required; this Compose stack does not expose it. Give that proxy a stable internal
address, place only that exact address in `API_TRUSTED_PROXY_IPS`, and pass
`X-Forwarded-Proto: https`. Wildcards and untrusted subnets are rejected. YooKassa deployment and
file-secret examples are documented in [yookassa.md](yookassa.md).

The permanent reservation service runs `python -m app.workers.reservation_expiry` and
sets `HEALTHCHECK_COMPONENT=reservation_expiry`, so its Docker healthcheck covers both
dependencies and its worker heartbeat. Both services inherit the same read-only
filesystem, dropped capabilities, resource limits, dependency ordering, internal
backend network, egress network, and rotated logs as the base application.

## Operational cautions

- Never use `docker compose down --volumes` during a normal deploy.
- Take an offsite backup and complete a test restore before applying migrations.
- Deploy a reviewed image digest, run `migrate`, then start application processes.
- A vulnerability scanner result, dependency advisory, expired backup, or overdue
  worker heartbeat is a release/operations failure, not an informational success.
