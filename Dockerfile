# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY requirements-prod.lock ./
RUN python -m pip install --require-hashes -r requirements-prod.lock \
    && python -m pip check

COPY --chown=10001:10001 pyproject.toml README.md LICENSE ./
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 alembic ./alembic

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

CMD ["python", "-m", "app.bot"]


FROM runtime AS test

USER root
COPY requirements.lock ./
RUN python -m pip install --require-hashes -r requirements.lock \
    && python -m pip check
COPY --chown=10001:10001 tests ./tests
USER 10001:10001

CMD ["pytest"]


# pg_dump must not be older than the PostgreSQL server. Keep this image's major
# version aligned with the Compose database image.
FROM postgres:18.4-bookworm AS backup

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates restic \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=runtime /usr/local /usr/local
COPY --from=runtime --chown=10001:10001 /app /app

WORKDIR /app
USER 10001:10001
ENTRYPOINT []
HEALTHCHECK NONE
CMD ["python", "-m", "app.maintenance.backup_restore", "backup"]
