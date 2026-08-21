# Безопасное развёртывание публичного демобота

Эта инструкция предназначена владельцу VPS. Публичное демо — отдельный runtime, а не экземпляр
рабочего бота. Оно не использует PostgreSQL и не должно получать production env или secrets.

## 1. Создайте отдельного Telegram-бота

1. Откройте официальный `@BotFather` и выполните `/newbot`.
2. Добавьте в имя пометку «Демо» и сохраните новый токен.
3. Никогда не используйте токен рабочего бота.
4. Сохраните токен на VPS в `.secrets/demo_bot_token`, а не в `.env.demo`.

## 2. Подготовьте файлы

```bash
cp .env.demo.example .env.demo
mkdir -p .secrets
openssl rand -base64 48 | tr -d '=+/\n' | head -c 48 > .secrets/demo_redis_password
printf '%s' 'ТОКЕН_ОТДЕЛЬНОГО_ДЕМОБОТА' > .secrets/demo_bot_token
chmod 600 .env.demo .secrets/demo_bot_token .secrets/demo_redis_password
```

Создайте SHA-256 отпечаток production-токена на доверенном компьютере:

```bash
printf %s "$PRODUCTION_BOT_TOKEN" | sha256sum
```

В `.env.demo` укажите только полученные 64 шестнадцатеричных символа в
`PRODUCTION_BOT_TOKEN_SHA256`. Сам production-токен в файл не копируйте. Заполните
`DEMO_SITE_URL` публичной HTTPS-ссылкой на страницу продукта.

В demo-конфигурации обязаны оставаться пустыми:

- `DATABASE_URL`;
- `ADMIN_TELEGRAM_IDS`;
- Sentry, YooKassa и Mini App параметры.

Запуск fail-fast завершится ошибкой, если задана база, совпал токен или включена production
интеграция.

## 3. Проверьте Compose

```bash
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml config --quiet
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml build
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml up -d
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml ps
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml logs --tail=100 bot
```

Должны появиться только два сервиса: `bot` и `redis`. Если Compose создаёт `postgres`, `migrate`,
`cleanup`, API или workers, используется неправильный compose-файл.

## 4. Защитные границы

- Redis находится во внутренней сети, не публикует порт и не сохраняет данные на диск.
- Для Redis включены пароль, лимит памяти `128mb` и вытеснение старых ключей.
- Контейнер бота работает не от root, с read-only filesystem, `cap_drop: ALL`, лимитами CPU,
  памяти и процессов.
- FSM имеет TTL; введённый текст заменяется маркером и не хранится.
- Middleware применяет индивидуальный и общий sliding-window rate limit и fail-closed replay guard.
- В demo package запрещены импорты БД/repositories и вызовы записи; это проверяется тестом.
- Production routers, сотрудники, платежные клиенты, рассылки и фоновые workers не создаются.

`demo-egress` нужен только для Telegram Bot API. Если инфраструктура поддерживает egress firewall,
разрешите контейнеру только DNS, HTTPS к Telegram и необходимые системные endpoints.

## 5. Обновление

Разворачивайте только проверенный commit отдельной demo-ветки или её отдельный immutable image.
Не заменяйте production tag образом демобота.

```bash
git fetch origin
git switch feat/public-demo-mode
git pull --ff-only origin feat/public-demo-mode
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml build --pull
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml up -d --remove-orphans
```

## 6. Приёмочная проверка

1. `/start` открывает три роли без выдачи доступа.
2. Клиентская запись проходит до подтверждения, но нигде не появляется.
3. Добавление окна, услуги и рассылки проходит по шагам, но не меняет карточки.
4. Переключатели функций и настройки неизменяемы.
5. Медиа и контакты не принимаются.
6. После перезапуска Redis навигация сбрасывается без потери каких-либо рабочих данных.
7. `docker compose ... ps` показывает healthy `bot` и `redis`.
8. В логах нет токенов, введённых значений или персональных данных.

Пользовательское описание: [DEMO_BOT_GUIDE.md](DEMO_BOT_GUIDE.md).
