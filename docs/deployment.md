# Развёртывание v0.4.5

## Подготовка

Скопируйте `.env.example` в `.env`. Обязательны `BOT_TOKEN`, URL PostgreSQL/Redis,
опубликованный `PRIVACY_POLICY_URL` и числовой `ADMIN_TELEGRAM_IDS` для первоначального
bootstrap владельца. Настоящие пароли PostgreSQL/Redis храните в `.secrets/postgres_password`
и `.secrets/redis_password`; пароль в URL остаётся placeholder и заменяется из смонтированного
file secret. После bootstrap runtime-права сотрудников берутся только из БД. `.env`, secrets,
дампы и логи с персональными данными не коммитятся.

```powershell
docker compose config
docker compose build
docker compose up -d
docker compose ps
docker compose logs bot notification-worker broadcast-worker reference-cleanup-worker privacy-deletion-worker reservation-worker migrate
docker compose run --rm bot python -m app.healthcheck
```

Сервисы:

- `postgres` и `redis` — stateful dependencies с healthchecks и named volumes;
- `migrate` — одноразовый `alembic upgrade head`;
- `bot` — aiogram long polling и Redis FSM;
- `notification-worker` — сервисные/review/repeat/waitlist задания;
- `broadcast-worker` — отдельная rate-limited очередь рекламных кампаний.
- `reference-cleanup-worker` — автоматическая очистка просроченных Telegram file ID.
- `privacy-deletion-worker` — ограниченная по попыткам обработка запросов обезличивания.

Все application-процессы используют один непривилегированный runtime image и стартуют
только после успешной миграции. `docker compose down` сохраняет volumes. Команда с
`--volumes` удаляет локальные данные и не должна использоваться при обычном обновлении.

## Обновление

1. Остановите application-процессы, не останавливая PostgreSQL и Redis.
2. Сделайте backup и тест восстановления:

   ```powershell
   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > before-upgrade.dump
   docker compose run --rm migrate alembic current
   ```

3. Получите проверенный commit/tag, выполните `docker compose build`.
4. Примените `docker compose run --rm migrate alembic upgrade head`.
5. Выполните healthcheck и smoke test, затем запустите процессы.
6. Для v0.3 проверьте динамические меню, фото-референсы, режим портфолио,
   модерацию отзывов и публикацию профиля мастера; рассылки оставьте
   выключенными до test-send.

Переход с v0.2 описан в
[migration-v0.2-to-v0.3.md](migration-v0.2-to-v0.3.md). Для более старой
установки сначала выполните переход из
[migration-v0.1-to-v0.2.md](migration-v0.1-to-v0.2.md).
Обновление v0.3.0 до v0.3.1 описано в
[migration-v0.3-to-v0.3.1.md](migration-v0.3-to-v0.3.1.md).

## Smoke test

- `/whoami` возвращает ожидаемый ID, `/admin` открывается только разрешённому ID;
- клиент проходит privacy/marketing onboarding и видит главное меню;
- каталог и окна читаются, тестовая запись создаётся и отменяется;
- кнопочный выбор даты/времени не принимает выходные и даты вне горизонта;
- фото-референсы сохраняются вместе с записью и видны только владельцу и админу;
- dry-run очистки показывает кандидатов без изменения данных, а execute обезличивает
  только просроченные строки;
- portfolio/CRM/waitlist/reviews открываются без ошибок, отключённые разделы скрыты;
- опубликованный профиль мастера виден клиенту, черновик — нет;
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
- контролируются возраст `pending/processing` заданий, успешность очистки референсов
  и перезапуски workers;
- публичная политика проверена владельцем и профильным специалистом;
- доступ к серверу ограничен, ОС/образы/зависимости обновляются;
- включение рассылок выполняется только после миграции, smoke test и test-send.

Проверка диска и потребления контейнеров на Linux VPS:

```bash
df -h
docker system df
docker stats
```

Не запускайте автоматический `docker system prune` из приложения: он может удалить
нужные образы и volumes. Для application-контейнеров Compose ограничивает json-логи
тремя файлами по 10 МБ.

GitHub Actions не является hosting. Нужен VPS/PaaS с постоянно работающими процессами и
устойчивыми PostgreSQL/Redis. Автоматический SSH deploy намеренно не включён без конкретной
модели сервера и управления секретами.
