# Тестирование

## Локальный quality gate

```powershell
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe app
.\.venv\Scripts\pytest.exe
```

## PostgreSQL integration

Используйте только отдельную БД, имя которой содержит `test`. Fixture выполняет
`TRUNCATE ... RESTART IDENTITY`, поэтому production URL здесь недопустим.

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://lanrouge:password@localhost:5432/lanrouge_test"
alembic upgrade head
.\.venv\Scripts\pytest.exe tests/integration
```

Интеграционный набор проверяет гонку бронирования, snapshot аудитории рассылки,
фильтрацию consent, append-only consent history и matching листа ожидания.

## Миграции и контейнеры

```powershell
alembic upgrade 20260722_0001
alembic upgrade head
alembic check
docker compose config
docker build --target runtime -t lanrouge-nails-bot:test .
docker build --target test -t lanrouge-nails-bot-tests:test .
```

CI выполняет те же проверки на Python 3.12 с PostgreSQL и Redis. Интеграционные тесты без
`TEST_DATABASE_URL` пропускаются намеренно; в CI переменная обязательна.
