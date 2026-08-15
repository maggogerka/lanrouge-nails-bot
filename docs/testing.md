# Тестирование

## Локальный quality gate

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy app
.\.venv\Scripts\python.exe -m pytest
```

## PostgreSQL integration

Используйте только отдельную БД, имя которой содержит `test`. Fixture выполняет
`TRUNCATE ... RESTART IDENTITY`, поэтому production URL здесь недопустим.

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://app_user:password@localhost:5432/app_test"
alembic upgrade head
.\.venv\Scripts\python.exe -m pytest tests/integration
```

Интеграционный набор проверяет гонку бронирования, snapshot аудитории рассылки,
фильтрацию consent, append-only consent history и matching листа ожидания.

## Миграции и контейнеры

```powershell
alembic upgrade 20260722_0001
alembic upgrade head
alembic check
docker compose config
docker build --target runtime -t telegram-crm-bot:test .
docker build --target test -t telegram-crm-bot-tests:test .
```

CI выполняет те же проверки на Python 3.12 с PostgreSQL и Redis. Интеграционные тесты без
`TEST_DATABASE_URL` пропускаются намеренно; в CI переменная обязательна.
