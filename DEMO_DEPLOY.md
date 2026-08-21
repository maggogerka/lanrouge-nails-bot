# Развёртывание публичного демобота

Это техническая инструкция для владельца сервера. Публичная пользовательская документация её не включает.

## 1. Создайте отдельного бота

1. Откройте официальный бот `@BotFather` в Telegram.
2. Выполните команду `/newbot`.
3. Укажите отображаемое имя с пометкой «Демо».
4. Укажите уникальное имя, которое заканчивается на `bot`.
5. Сохраните токен только в локальном `.env.demo`. Не добавляйте его в Git и не используйте токен рабочего бота.
6. Через `/setdescription`, `/setabouttext` и `/setuserpic` добавьте описание и логотип.

## 2. Подготовьте конфигурацию

```bash
cp .env.demo.example .env.demo
mkdir -p .secrets
openssl rand -base64 48 | tr -d '=+/\n' | head -c 48 > .secrets/demo_postgres_password
openssl rand -base64 48 | tr -d '=+/\n' | head -c 48 > .secrets/demo_redis_password
chmod 600 .env.demo .secrets/demo_postgres_password .secrets/demo_redis_password
```

Заполните `.env.demo`:

- `BOT_TOKEN` — токен отдельного демобота;
- `DATABASE_URL` — адрес отдельной базы, имя обязательно содержит `demo`;
- `REDIS_URL` — адрес отдельного Redis; пароль будет подставлен из Docker secret;
- `PRODUCTION_BOT_TOKEN_SHA256` — SHA-256 рабочего токена;
- `PRODUCTION_DATABASE_URL_SHA256` — SHA-256 полного рабочего URL базы;
- `DEMO_SITE_URL` — публичная HTTPS-ссылка на сайт;
- `TIMEZONE` — часовой пояс бизнеса;
- остальные `DEMO_*` — срок сессии, очистки, пауза сброса и лимит действий.

Отпечатки создаются локально без записи рабочего секрета в файл:

```bash
printf %s "$PRODUCTION_BOT_TOKEN" | sha256sum
printf %s "$PRODUCTION_DATABASE_URL" | sha256sum
```

Вставьте только 64 шестнадцатеричных символа. Сам рабочий токен и URL не должны находиться в `.env.demo`.

## 3. Проверка и локальный запуск

```bash
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml config --quiet
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml build
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml up -d
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml ps
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml logs --tail=100 bot
```

Compose создаёт только demo-контейнеры, внутреннюю сеть, отдельные volumes и ротируемые логи. Production Compose не подключается.

## 4. Запуск на VPS

Установите Docker Engine и Compose plugin, скопируйте репозиторий на сервер, создайте `.env.demo` и два secret-файла. Затем выполните те же команды с фиксированным project name:

```bash
cd /opt/crm-public-demo
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml pull --ignore-buildable
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml build --pull
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml up -d --remove-orphans
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml ps
```

Контейнер `bot` ограничен 0.5 CPU и 512 MB, `cleanup` — 0.25 CPU и 256 MB. Для автозапуска используется `restart: unless-stopped`. Отдельный cleanup-процесс ежечасно удаляет только устаревшие строки `demo_*`.

## 5. Обновление и остановка

```bash
git pull --ff-only
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml build
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml up -d --remove-orphans
```

Остановка без удаления данных:

```bash
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml down
```

Удаление demo-volumes необратимо и выполняется только при осознанном полном сбросе:

```bash
docker compose -p crm-public-demo --env-file .env.demo -f compose.demo.yml down --volumes
```

## 6. Обязательная ручная проверка

- рабочий бот продолжает отвечать независимо;
- demo token и database URL проходят fail-fast проверки;
- два Telegram-аккаунта не видят данные друг друга;
- запись из клиентского режима появляется в панели того же пользователя;
- сброс одного аккаунта не меняет другой;
- платежи и рассылки показывают имитацию и не выполняются;
- старые кнопки после сброса отклоняются;
- даты и свободные окна актуальны;
- `bot` здоров, а логи не содержат токенов и персональных данных.
