# Развёртывание

## Локальный Docker Compose

1. Скопировать `.env.example` в `.env`.
2. Заполнить Telegram token, PostgreSQL password и остальные параметры.
3. Выполнить `docker compose config` и убедиться, что конфигурация корректна.
4. Запустить `docker compose up --build`.
5. Проверить `docker compose ps` и структурированные логи `docker compose logs bot`.

Сервис `migrate` ждёт готовности PostgreSQL, выполняет `alembic upgrade head` и завершается. Bot стартует только после успешных миграций и готовности PostgreSQL/Redis.

Данные PostgreSQL и Redis сохраняются в именованных volumes. `docker compose down` их не удаляет. Команда с `--volumes` удаляет локальные данные и должна использоваться только осознанно.

## Production target

Для production требуется VPS или PaaS, поддерживающая постоянно работающий Python-процесс, PostgreSQL и Redis. GitHub Pages для этого непригоден.

Перед production-развёртыванием необходимо:

- заменить все демонстрационные пароли и хранить секреты в secret manager/защищённом env;
- опубликовать и проверить `PRIVACY_POLICY_URL`;
- настроить резервное копирование PostgreSQL и проверку восстановления;
- ограничить сетевой доступ к PostgreSQL и Redis;
- настроить TLS/webhook либо оставить защищённый long polling процесс;
- настроить сбор логов, мониторинг, оповещения и ротацию;
- выполнить миграции отдельным release job;
- запускать bot/worker непривилегированным пользователем;
- провести аудит персональных данных и зависимостей.

Автоматический SSH deployment workflow не добавляется до предоставления конкретного сервера и модели управления секретами.
