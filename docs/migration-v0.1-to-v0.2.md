# Миграция v0.1.0 → v0.2.0

## Перед обновлением

1. Остановите bot и workers, оставив PostgreSQL доступным.
2. Сделайте проверенный backup (`pg_dump -Fc`) и запишите вывод `alembic current`.
3. Разверните код v0.2.0, но оставьте `broadcasts_enabled=false`.
4. Убедитесь, что `.env` содержит прежние обязательные переменные и одинаковый пароль в
   `POSTGRES_PASSWORD`/`DATABASE_URL`.

## Применение

Исходная ревизия v0.1 — `20260722_0001`, head v0.2 — `20260722_0005`.

```powershell
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate alembic current
docker compose run --rm bot python -m app.healthcheck
docker compose up -d bot notification-worker broadcast-worker
```

Проверьте `/start`, `/admin`, услуги, ближайшие окна, portfolio, CRM, waitlist и test-send
рассылки. Только после smoke test включайте рассылки в админ-настройках.

## Что добавляется

Последовательные миграции создают CRM/consent history, portfolio, waitlist/reviews и
broadcast tables, а также совместимые nullable/default-safe поля существующих сущностей.
Миграция не меняет исходную ревизию v0.1 и сохраняет пользователей, записи и задания.

## Rollback

Предпочтительный rollback приложения: снова развернуть v0.1 и восстановить сделанный до
upgrade backup в отдельную проверенную БД. Не выполняйте downgrade вслепую: новые native
PostgreSQL enum values и v0.2-данные нельзя безопасно удалить без анализа. Если миграция не
завершилась, сохраните логи/текущую ревизию, остановите процессы и восстановите backup.
