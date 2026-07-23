# Миграция v0.3.0 → v0.3.1

1. Сделайте резервную копию PostgreSQL и проверьте её восстановление.
2. Добавьте пять `REFERENCE_*` переменных из `.env.example` либо оставьте безопасные
   значения по умолчанию.
3. Соберите новый image и примените миграцию:

   ```powershell
   docker compose build
   docker compose run --rm migrate alembic upgrade head
   docker compose run --rm migrate alembic current
   ```

4. Проверьте будущую очистку без изменения данных:

   ```powershell
   docker compose run --rm bot python -m app.maintenance.cleanup_references --dry-run
   ```

5. Запустите сервисы и проверьте worker:

   ```powershell
   docker compose up -d
   docker compose ps
   docker compose logs --tail 100 reference-cleanup-worker
   ```

Миграция добавляет `expires_at`, счётчики очистки и singleton health-state, затем
пересчитывает срок существующих строк по статусу Appointment. Она не скачивает и не
удаляет фотографии во время upgrade.

После первой выполненной очистки автоматический downgrade запрещён: удалённые Telegram
ID невозможно восстановить. Для отката используйте проверенный pre-upgrade dump и
предыдущий application image.
