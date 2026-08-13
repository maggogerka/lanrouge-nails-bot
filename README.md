# White-label Telegram CRM bot

Production-ready Telegram CRM и онлайн-запись для частного мастера или салона.
Текущая версия — **v0.4.1**. Runtime-интерфейс получает бренд и опубликованное
приветствие из БД.

## Что умеет v0.4.1

- один владелец-мастер или несколько мастеров с индивидуальными услугами и расписанием;
- роли `OWNER`, `MANAGER`, `MASTER`, `RECEPTIONIST` с проверкой членства в БД на каждом действии;
- одноразовые приглашения сотрудников, отзыв и аудит;
- независимые расписания, недельные интервалы, исключения и защита PostgreSQL от пересечений;
- запись, отмена, перенос, напоминания, CRM, портфолио, отзывы, лист ожидания и рассылки;
- feature flags бизнеса и динамические меню;
- отключённая, ручная или YooKassa-предоплата, временный резерв, webhook и возвраты;
- заявки на удаление данных, versioned consent и контролируемая анонимизация;
- восстановимое обезличивание с блокировками, ограниченными попытками и отдельным worker;
- черновик, фото, предпросмотр и публикация универсального приветствия;
- безопасная CRM-карточка с актуальным Telegram username и permissioned-телефоном;
- campaign deep links и обезличенная воронка источников;
- отдельный защищённый `/api/v1` для будущего Mini App;
- Sentry с очисткой данных, heartbeat health checks, зашифрованный offsite backup и restore test.

## Быстрый локальный запуск

Требуются Python 3.12, Docker Desktop с Compose и Telegram-бот от `@BotFather`.

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force .secrets
Set-Content -NoNewline .secrets/postgres_password "replace-with-long-random-secret"
Set-Content -NoNewline .secrets/redis_password "replace_with_32_byte_url_safe_secret"
```

В `.env` обязательно задайте `BOT_TOKEN`, `PRIVACY_POLICY_URL`, `DATABASE_URL` и
аутентифицированный `REDIS_URL`. Пароли внутри URL должны совпадать с файлами в `.secrets`.
Настоящие `.env` и `.secrets` игнорируются Git; не отправляйте их в чат и не коммитьте.

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs -f bot
```

Базовый Compose запускает PostgreSQL, Redis, миграции, бота и постоянные workers.
Опциональные API и очистка просроченных резервов:

```powershell
docker compose -f docker-compose.yml -f compose.profiles.yml --profile api --profile reservation up --build -d
```

Production hardening и backup подключаются отдельным override:

```powershell
docker compose -f docker-compose.yml -f compose.production.yml config --quiet
docker compose -f docker-compose.yml -f compose.production.yml up -d
```

Полные команды и границы секретов: [docs/deployment-v0.4.md](docs/deployment-v0.4.md).

## Первый владелец и сотрудники

1. Отправьте `/whoami` и запишите числовой ID в `ADMIN_TELEGRAM_IDS`.
2. Пересоздайте bot-контейнер: `docker compose up -d --force-recreate bot`.
3. На старте ID однократно bootstrap'ится как `OWNER` бизнеса №1.
4. После bootstrap переменная **не является runtime-списком доступа**. `/admin` и `/master`
   каждый раз проверяют активный `StaffMember`, роль и tenant scope в БД.
5. Следующих сотрудников приглашайте через «Мастера и сотрудники»; ссылка одноразовая и
   действует 24 часа по умолчанию.

Владелец, который сам принимает клиентов, открывает свою карточку в «Мастера и сотрудники»
и включает «Принимать записи». Ручной переключатель `solo/salon` не нужен: режим определяется
автоматически по числу активных специалистов. В карточке каждого мастера настраиваются фото,
описание, услуги и до пяти HTTPS-ссылок; первая ссылка используется кнопкой «Написать мастеру».

Адрес салона, отдельная ссылка на карту, телефон, часовой пояс и до пяти источников поддержки
настраиваются в «Настройки бизнеса». Эти данные используются подтверждениями и клиентским
разделом «Поддержка и контакты»; старые глобальные адрес и контакт мастера не подставляются.

Несколько bootstrap-ID разделяются запятыми: `123456789,987654321`. Username Telegram не
используется для авторизации.

## Платежи

`DISABLED` сразу подтверждает запись. `MANUAL` резервирует слот и показывает безопасную
инструкцию владельца; сотрудник явно подтверждает фактическое получение денег. `YOOKASSA`
использует отдельные credentials конкретного бизнеса, идемпотентный provider flow и проверенный
webhook с обязательным authoritative GET. Карточные данные, CVV, SMS-коды и полные provider
payload не сохраняются.

YooKassa опциональна: API и ручная оплата запускаются без неё. Если задана хотя бы часть
`YOOKASSA_*`, конфигурация должна быть полной. Подробно:
[docs/payments.md](docs/payments.md) и [docs/yookassa.md](docs/yookassa.md).

## Политика и согласия

- ссылка/версия политики хранятся у `Business`; fallback для первого запуска —
  `PRIVACY_POLICY_URL`;
- versioned текст отдельного согласия на рассылку находится в
  `app/domain/legal.py` (`MARKETING_CONSENT_*`);
- решения и повторное согласие обслуживает `app/services/consent_service.py`;
- append-only история — `consent_history`, заявки на удаление — `data_deletion_requests`;
- клиент управляет рассылкой и удалением через раздел «Конфиденциальность».

Технические тексты не заменяют юридическое утверждение политики, оферты, отмены и возврата.

## Разработка и проверки

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock
Copy-Item .env.example .env
alembic upgrade head
python -m app.bot
```

```powershell
ruff format --check .
ruff check .
mypy app
pytest
pytest --cov=app --cov-report=term-missing --cov-fail-under=60
bandit --recursive app --severity-level medium --confidence-level medium
pip-audit --require-hashes --disable-pip -r requirements-prod.lock
```

PostgreSQL integration tests требуют отдельную БД с `test` в имени через
`TEST_DATABASE_URL`. CI дополнительно проверяет чистую миграцию, копию схемы v0.3.1,
Compose/Docker, gitleaks и Trivy. См. [docs/testing-v0.4.md](docs/testing-v0.4.md).

## Документация

- [архитектура v0.4](docs/architecture.md)
- [миграция v0.3.1 → v0.4.0](docs/migration-v0.3.1-to-v0.4.md)
- [Mini App API](docs/mini-app-plan.md)
- [production deployment](docs/deployment-v0.4.md)
- [платежи](docs/payments.md) и [YooKassa](docs/yookassa.md)
- [backup/restore](docs/backup-restore.md)
- [мониторинг](docs/monitoring.md)
- [ротация секретов](docs/secrets-rotation.md)
- [incident response](docs/incident-response.md)
- [privacy](docs/privacy.md)

## Осознанные границы релиза

- большой frontend Mini App не входит в v0.4.0; готов backend и ADR;
- YooKassa нельзя включать до получения shop ID/secret и публичного HTTPS webhook;
- offsite backup явно выключен, пока не заданы restic repository и credentials;
- юридические тексты должны утвердить владелец бизнеса и профильный специалист;
- CRM-подписка отделена от платежей клиентов; рублёвая продажа цифровой подписки внутри
  Telegram не реализована.

## Лицензия

[MIT](LICENSE)
