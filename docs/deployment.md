# Развёртывание v0.2.0

## Подготовка

Скопируйте `.env.example` в `.env`. Обязательны `BOT_TOKEN`, `POSTGRES_PASSWORD`,
`DATABASE_URL`, `REDIS_URL`, опубликованный `PRIVACY_POLICY_URL` и хотя бы один числовой
`ADMIN_TELEGRAM_IDS`. Пароль после `lanrouge:` в `DATABASE_URL` должен совпадать с
`POSTGRES_PASSWORD`. `.env`, дампы и логи с персональными данными не коммитятся.

```powershell
docker compose config
docker compose build
docker compose up -d
docker compose ps
docker compose logs bot notification-worker broadcast-worker migrate
docker compose run --rm bot python -m app.healthcheck
```

Сервисы:

- `postgres` и `redis` — stateful dependencies с healthchecks и named volumes;
- `migrate` — одноразовый `alembic upgrade head`;
- `bot` — aiogram long polling и Redis FSM;
- `notification-worker` — сервисные/review/repeat/waitlist задания;
- `broadcast-worker` — отдельная rate-limited очередь рекламных кампаний.

Все application-процессы используют один непривилегированный runtime image и стартуют
только после успешной миграции. `docker compose down` сохраняет volumes. Команда с
`--volumes` удаляет локальные данные и не должна использоваться при обычном обновлении.

## Обновление

1. Остановите `bot`, `notification-worker`, `broadcast-worker`.
2. Сделайте backup и тест восстановления:

   ```powershell
   docker compose exec -T postgres pg_dump -U lanrouge -d lanrouge -Fc > lanrouge-before-upgrade.dump
   docker compose run --rm migrate alembic current
   ```

3. Получите проверенный commit/tag, выполните `docker compose build`.
4. Примените `docker compose run --rm migrate alembic upgrade head`.
5. Выполните healthcheck и smoke test, затем запустите процессы.
6. Для v0.2 оставьте рассылки выключенными до test-send.

Подробный переход с v0.1 описан в
[migration-v0.1-to-v0.2.md](migration-v0.1-to-v0.2.md).

## Smoke test

- `/whoami` возвращает ожидаемый ID, `/admin` открывается только разрешённому ID;
- клиент проходит privacy/marketing onboarding и видит главное меню;
- каталог и окна читаются, тестовая запись создаётся и отменяется;
- portfolio/CRM/waitlist/reviews открываются без ошибок;
- test-send рассылки получает только текущий администратор;
- в логах workers нет повторяющихся ошибок подключения или просроченных lease.

## Rollback

При ошибке приложения выключите процессы, сохраните логи и `alembic current`. Если схема
совместима, разверните предыдущий application image. При несовместимой/частичной миграции
восстановите pre-upgrade dump в проверенную PostgreSQL-базу и переключите `DATABASE_URL`.
Не делайте автоматический downgrade без анализа v0.2-данных и native enum: он может быть
необратим без потери новых записей.

## Production checklist

- секреты хранятся в secret manager/защищённом env, все demo-пароли заменены;
- PostgreSQL/Redis не опубликованы в интернет, backups шифруются и регулярно
  восстанавливаются на проверке;
- настроены сбор структурированных логов, мониторинг, alerting и ротация;
- контролируются возраст `pending/processing` заданий и перезапуски обоих workers;
- публичная политика проверена владельцем и профильным специалистом;
- доступ к серверу ограничен, ОС/образы/зависимости обновляются;
- включение рассылок выполняется только после миграции, smoke test и test-send.

GitHub Actions не является hosting. Нужен VPS/PaaS с постоянно работающими процессами и
устойчивыми PostgreSQL/Redis. Автоматический SSH deploy намеренно не включён без конкретной
модели сервера и управления секретами.
