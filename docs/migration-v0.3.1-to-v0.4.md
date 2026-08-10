# Миграция v0.3.1 → v0.4.0

Миграции `0011`–`0015` являются forward-only: downgrade запрещён, потому что он мог бы удалить
staff, расписание, финансовый аудит или privacy workflow. Rollback приложения выполняется только
вместе с восстановлением проверенного backup v0.3.1.

## Перед обновлением

1. Остановите операции записи и сделайте зашифрованный offsite backup.
2. Восстановите backup в отдельную БД с `test`/`restore` в имени.
3. Сохраните текущий image digest и `.env`/secret references (не значения в Git).
4. Проверьте свободное место и доступность PostgreSQL extension `btree_gist`.

## Применение

```powershell
docker compose run --rm migrate
docker compose run --rm bot alembic current
docker compose run --rm bot alembic check
```

- `0011`: Business, clients, staff, invitations, feature flags и bootstrap бизнеса №1;
- `0012`: tenant/staff scope существующих услуг, окон, записей и CRM;
- `0013`: недельное расписание, исключения, категории и назначения услуг;
- `0014`: reservations, payments/refunds/webhook inbox и отдельная CRM subscription;
- `0015`: data deletion workflow, versioned consent attribution и campaign sources.

Существующие пользователи, услуги, записи и snapshots сохраняются. Старые admin IDs создают
OWNER только при startup bootstrap; затем runtime права берутся из `staff_members`.

## После обновления

1. Запустите bot/workers, затем reservation worker; API — после настройки exact hosts/origins.
2. Проверьте `/admin`, `/master`, роли, одну старую запись и создание новой записи.
3. Оставьте payment mode `DISABLED`, пока manual/YooKassa flow не пройдёт smoke test.
4. Проверьте heartbeat, Sentry scrubbing и статус backup.
5. Не удаляйте backup до завершения бизнес-приёмки.

CI воспроизводит clean database → head и seeded v0.3.1 schema → head с проверкой сохранности
данных. Локальный destructive preservation test запускается только на отдельной БД через
`MIGRATION_PRESERVATION_TEST=1`.
