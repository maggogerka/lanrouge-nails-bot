# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN python -m pip install --no-cache-dir .

USER app

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

CMD ["python", "-m", "app.bot"]

FROM runtime AS test

USER root
COPY --chown=app:app tests ./tests
RUN python -m pip install --no-cache-dir ".[dev]"
RUN chown app:app /app
USER app

CMD ["pytest"]
