# lanrouge nails bot

Telegram CRM and appointment booking bot for **lanrouge nails**: ручные окна доступности, каталог услуг, транзакционная запись, отмена/перенос, напоминания и клиентские записи.

> Статус: MVP v0.1.0 реализован и проходит pre-release проверку. Для production-запуска остаются операционные действия владельца: реальные секреты, опубликованная политика конфиденциальности, hosting, backups и мониторинг.

## Возможности v0.1.0

- авторизация администратора только по числовому Telegram ID;
- отдельное согласие на обработку данных и рекламу;
- управление услугами, ценами и диапазонами длительности;
- ручное создание открытых окон;
- защищённое от гонок бронирование;
- просмотр, отмена и перенос записей;
- правило самостоятельной отмены за 36 часов;
- персистентные напоминания за 24 часа, 3 часа и 1 час;
- расписание и основные настройки администратора.

## Архитектура

aiogram handlers являются транспортным слоем. Бизнес-правила находятся в services/domain, SQLAlchemy-запросы — в repositories, а транзакции координируются Unit of Work. PostgreSQL является источником истины; Redis хранит FSM. Bot и reminder worker запускаются отдельными процессами.

Подробности:

- [архитектура](docs/architecture.md);
- [правила бронирования](docs/booking-rules.md);
- [спорные предположения](docs/assumptions.md);
- [развёртывание](docs/deployment.md);
- [обработка персональных данных](docs/privacy.md);
- [roadmap](docs/roadmap.md).

## Стек

Python 3.12, aiogram 3, PostgreSQL, SQLAlchemy 2 async, asyncpg, Alembic, Redis, Pydantic Settings, Docker Compose, pytest, Ruff, mypy и GitHub Actions.

## Требования

- Git;
- Docker Desktop с Docker Compose — рекомендуемый путь;
- либо Python 3.12, PostgreSQL и Redis для запуска без контейнеров;
- Telegram-бот, созданный через BotFather.

## Настройка BotFather

1. Откройте `@BotFather` и выполните `/newbot`.
2. Задайте отображаемое имя и уникальный username.
3. Скопируйте токен только в локальный `.env`; не отправляйте его в чат и не коммитьте.
4. При необходимости задайте команды `/setcommands`:

   ```text
   start - открыть главное меню
   whoami - показать мой Telegram ID
   admin - открыть меню администратора
   delete_my_data - запросить удаление или анонимизацию данных
   ```

## Настройка окружения

```powershell
Copy-Item .env.example .env
```

Заполните как минимум `BOT_TOKEN` и безопасный `POSTGRES_PASSWORD`. Значения `DATABASE_URL` и `REDIS_URL` в примере рассчитаны на Docker Compose. Настоящий `.env` игнорируется Git.

`ADMIN_TELEGRAM_IDS` при первом запуске можно оставить пустым: административные handlers будут закрыты, но `/whoami` останется доступной. Владелец отправляет `/whoami`, копирует числовой ID в `.env` и перезапускает bot.

Перед включением клиентского сценария нужен публичный `PRIVACY_POLICY_URL`.

## Запуск через Docker Compose

```powershell
docker compose up --build
```

Compose запускает PostgreSQL, Redis, применяет Alembic migrations и затем запускает bot и персистентный reminder worker. Проверка зависимостей:

```powershell
docker compose run --rm bot python -m app.healthcheck
```

Остановка без удаления данных:

```powershell
docker compose down
```

## Локальный запуск на Windows 10

Установите Python 3.12, PostgreSQL и Redis, затем:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
python -m app.bot
```

Во втором терминале с тем же окружением запустите обработчик напоминаний:

```powershell
python -m app.workers.reminders
```

Для локальных PostgreSQL/Redis замените имена `postgres` и `redis` в URL на `localhost`. Частота опроса, размер batch, максимальное число попыток и lease worker настраиваются переменными `REMINDER_*` из `.env.example`.

## Миграции

```powershell
alembic upgrade head
alembic current
```

Новая миграция после изменения моделей:

```powershell
alembic revision --autogenerate -m "describe change"
```

## Проверки

```powershell
ruff format --check .
ruff check .
mypy app
pytest
```

Для автоформатирования используйте `ruff format .`. CI выполняет проверки на Python 3.12 с настоящим PostgreSQL, предварительно применяет миграции и запускает конкурентный тест бронирования.

Интеграционные тесты намеренно пропускаются без `TEST_DATABASE_URL`. В этом URL разрешена только отдельная база, имя которой содержит `test`: fixture очищает её таблицы. Пример ручного запуска после применения миграций:

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://lanrouge:password@localhost:5432/lanrouge_test"
pytest tests/integration
```

## Роли

- **Клиент** — любой пользователь Telegram, принявший необходимые условия. Может работать только со своими записями.
- **Администратор** — пользователь, чей числовой Telegram ID находится в `ADMIN_TELEGRAM_IDS`. Username и роль в базе не предоставляют административных полномочий.

## Ограничения первой версии

- long polling вместо webhook;
- один мастер и одна студия;
- нет предоплаты, Mini App, портфолио, статистики и массовых рассылок;
- GitHub хранит код и запускает CI, но не является hosting-платформой для процесса bot/PostgreSQL;
- Telegram API не позволяет строго гарантировать exactly-once внешнюю доставку сообщения после аварийного сбоя.

## Персональные данные

Проект хранит Telegram ID, контакт, согласия и данные записей. Никогда не используйте реальные клиентские данные в тестах и не публикуйте `.env`, дампы БД или application logs. [Технический privacy draft](docs/privacy.md) не заменяет юридически утверждённую политику.

## Roadmap

Следующие версии планируют портфолио, рассылки, лист ожидания, клиентские карточки, статистику, лояльность и Telegram Mini App. Детали — в [roadmap](docs/roadmap.md).

## Лицензия

[MIT](LICENSE). Перед публичным распространением следует отдельно подтвердить выбор лицензии владельцем.
