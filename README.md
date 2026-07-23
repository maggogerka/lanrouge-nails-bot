# lanrouge nails bot

Production-ready Telegram CRM и бот онлайн-записи для частного мастера **lanrouge nails**. Текущая версия — **v0.3.1**.

В v0.3.1 добавлена управляемая политика хранения фото-референсов: сроки зависят
от статуса записи, отдельный worker автоматически обезличивает просроченные
Telegram file ID, а администратор и клиент могут удалить доступ бота вручную.

## Возможности

- безопасная запись в ручные окна, отмена, перенос и сервисные напоминания;
- админ-панель `/admin`, доступная только числовым ID из `ADMIN_TELEGRAM_IDS`;
- портфолио с фотографиями, тегами, deep links и привязкой дизайна к записи;
- CRM-карточки клиентов, теги, приватные заметки и блокировка самостоятельной записи;
- лист ожидания с персистентными уведомлениями о подходящих окнах;
- отзывы после завершённого визита и отдельное согласие на публикацию;
- повторная запись по последней услуге с текущей ценой;
- сегментированные рекламные рассылки с preview, test-send, явным подтверждением,
  замороженной аудиторией, лимитом скорости и повторами;
- независимые настройки рекламных и repeat-уведомлений с append-only историей согласий.
- автоматическое и ручное удаление доступа бота к устаревшим фото-референсам.

## Быстрый запуск в Docker

Требуются Git, Docker Desktop с Compose и бот от `@BotFather`.

```powershell
Copy-Item .env.example .env
```

Заполните в `.env`:

```dotenv
BOT_TOKEN=токен_из_BotFather
ADMIN_TELEGRAM_IDS=ваш_числовой_Telegram_ID
PRIVACY_POLICY_URL=https://example.com/privacy
POSTGRES_PASSWORD=длинный_случайный_пароль
DATABASE_URL=postgresql+asyncpg://lanrouge:тот_же_пароль@postgres:5432/lanrouge
```

`POSTGRES_PASSWORD` и пароль внутри `DATABASE_URL` должны совпадать. Настоящий `.env`
игнорируется Git и не должен отправляться в чат или попадать в коммит.

```powershell
docker compose config
docker compose up --build -d
docker compose ps
docker compose logs -f bot notification-worker broadcast-worker reference-cleanup-worker
```

Compose поднимает PostgreSQL, Redis, одноразовый `migrate`, бот и отдельные workers
уведомлений, рассылок и очистки референсов. Проверка подключений без Telegram polling:

```powershell
docker compose run --rm bot python -m app.healthcheck
```

Остановка без удаления данных:

```powershell
docker compose down
```

## Администратор и мастер

1. Отправьте боту `/whoami` и скопируйте числовой ID.
2. Запишите его в `.env`: `ADMIN_TELEGRAM_IDS=123456789`. Несколько ID разделяются
   запятыми: `123456789,987654321`.
3. После изменения `.env` обязательно пересоздайте процесс бота:

   ```powershell
   docker compose up -d --force-recreate bot
   ```

4. Отправьте `/admin`. Админские кнопки не заменяют клиентское меню: панель открывается
   этой командой и закрыта фильтром по env-списку. Поле `role` в БД и username сами по
   себе прав не дают. В текущей модели один и тот же авторизованный пользователь является
   владельцем/мастером и администратором.

Если панель не появилась, проверьте `docker compose exec bot printenv ADMIN_TELEGRAM_IDS`
и что ID указан без `@`, пробелов и кавычек, затем пересоздайте контейнер.

## Политика и согласие на рассылку

- публичная юридически утверждённая политика размещается владельцем по адресу из
  `PRIVACY_POLICY_URL`;
- технический draft и границы обработки описаны в [docs/privacy.md](docs/privacy.md);
- текст onboarding находится в `app/handlers/client/onboarding.py` (`_PRIVACY_TEXT` и
  `_MARKETING_TEXT`), кнопки — в `app/keyboards/client/consent.py`;
- решения сохраняет `app/services/consent_service.py`, текущие timestamps лежат в
  `users`, а неизменяемая история — в `consent_history`;
- клиент в любой момент меняет рекламную подписку через «🔔 Настройки уведомлений».
  Отказ от рекламы не отключает сервисные сообщения по действующей записи.

Технический draft не заменяет проверку политики владельцем и профильным специалистом до
production-запуска.

## Локальная разработка

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
python -m app.bot
```

В отдельных терминалах:

```powershell
python -m app.workers.reminders
python -m app.workers.broadcasts
python -m app.workers.reference_cleanup
```

Для запуска вне Compose замените хосты `postgres` и `redis` на `localhost`.

## Проверки

```powershell
ruff format --check .
ruff check .
mypy app
pytest
```

Интеграционные тесты требуют отдельную БД с `test` в имени через
`TEST_DATABASE_URL`; fixture очищает её таблицы. Полная инструкция —
[docs/testing.md](docs/testing.md).

## Документация

- [архитектура](docs/architecture.md)
- [portfolio](docs/portfolio.md)
- [CRM](docs/crm.md)
- [лист ожидания](docs/waitlist.md)
- [рассылки](docs/broadcasts.md)
- [правила бронирования](docs/booking-rules.md)
- [выбор даты и времени](docs/date-time-picker.md)
- [фото-референсы записи](docs/booking-reference-media.md)
- [retention и очистка референсов](docs/reference-retention.md)
- [управление отзывами](docs/review-administration.md)
- [режимы портфолио](docs/portfolio-modes.md)
- [профиль мастера](docs/master-profile.md)
- [миграция v0.2 → v0.3](docs/migration-v0.2-to-v0.3.md)
- [миграция v0.3.0 → v0.3.1](docs/migration-v0.3-to-v0.3.1.md)
- [миграция v0.1 → v0.2](docs/migration-v0.1-to-v0.2.md)
- [развёртывание и rollback](docs/deployment.md)
- [privacy и consent](docs/privacy.md)
- [тестирование](docs/testing.md)

## Ограничения

- один мастер и одна студия, long polling, без предоплаты и Mini App;
- Telegram Bot API не предоставляет идемпотентный ключ отправки, поэтому после аварии
  между успешной отправкой и commit теоретически возможен редкий дубль;
- рассылки по умолчанию выключены настройкой бизнеса и включаются администратором только
  после миграции и smoke test;
- GitHub Actions проверяет код, но сам по себе не является hosting для постоянно
  работающих bot/PostgreSQL/Redis.

## Лицензия

[MIT](LICENSE).
