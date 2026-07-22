# Миграция v0.2.0 → v0.3.0

## До обновления

1. Создать и проверить резервную копию PostgreSQL.
2. Зафиксировать `alembic current`, версию приложения и число ключевых строк.
3. Остановить bot/workers либо включить согласованное maintenance window.
4. Убедиться, что текущая revision — `20260722_0005`.

## Последовательность ревизий

1. `0006_v030_ux_settings`: типизированные настройки и portfolio mode с backfill.
2. `0007_v030_booking_reference_media`: reference media.
3. `0008_v030_review_administration`: review revisions и soft-delete поля.
4. `0009_v030_master_profile`: профиль и публичные ссылки.

Каждая миграция должна поддерживать clean database → head и upgrade с v0.1/v0.2. Применённые `0001–0005` не редактируются.

## Проверка после upgrade

- `alembic current` показывает head;
- `alembic check` не предлагает новых операций;
- сохранены users, services, windows, appointments, reviews и portfolio;
- `portfolio_mode` соответствует прежнему `portfolio_enabled`;
- bot, notification-worker и broadcast-worker проходят health/smoke test;
- новые feature flags сначала используют безопасные defaults.

## Откат

Downgrade выполняется только после preflight: новые таблицы не должны содержать production-данные, которые будут потеряны. При наличии reference media, revisions или profile rows автоматический downgrade обязан остановиться либо требовать заранее выполненный экспорт. Native enum удаляется только после преобразования всех зависимых значений.

Обновление приложения без резервной копии production-БД не поддерживается.
