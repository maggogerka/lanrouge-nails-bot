# ADR: HTTP API и подготовка Telegram Mini App

Статус: **backend реализован в v0.4.0**. Полноценный frontend и внешний HTTPS ingress
не входят в релиз.

## Решение

HTTP boundary реализуется как небольшой ASGI-компонент без привязки бизнес-логики к web framework.
Это сохраняет versioned API и проверяемые security boundaries. ASGI-слой не содержит
бизнес-правил: Telegram handlers и будущие HTTP
endpoint'ы вызывают одни и те же application services и Unit of Work.

Текущая реализация находится в `app/api/` и предоставляет:

| Метод и путь | Назначение | Авторизация |
|---|---|---|
| `GET /health/live` | процесс принимает запросы | нет |
| `GET /health/ready` | безопасная boolean-проекция DB/Redis/workers | нет |
| `GET /api/v1` | discovery версии API | rate limit |
| `POST /api/v1/auth/telegram` | одноразовый обмен raw `initData` на серверную сессию | Telegram HMAC + TTL + replay guard |
| `POST /api/v1/webhooks/yookassa` | недоверенная входящая нотификация | network boundary + authoritative provider GET |
| `GET /api/v1/business` | white-label профиль бизнеса | opaque bearer session |
| `GET /api/v1/services` | активный каталог | bearer + privacy consent |
| `GET /api/v1/masters?service_id=…` | назначенные мастера услуги | opaque bearer session |
| `GET /api/v1/availability/dates` | доступные локальные даты | opaque bearer session |
| `GET /api/v1/availability/slots` | слоты выбранной даты | opaque bearer session |
| `POST /api/v1/reservations` | атомарный checkout/резерв | bearer + `Idempotency-Key` |
| `POST /api/v1/appointments` | идемпотентный alias checkout | bearer + `Idempotency-Key` |
| `GET /api/v1/appointments` | записи текущего клиента | opaque bearer session |
| `POST /api/v1/appointments/{id}/cancel` | самостоятельная отмена | opaque bearer session |
| `POST /api/v1/appointments/{id}/reschedule` | перенос | opaque bearer session |
| `GET /api/v1/payments/{id}` | принадлежащий клиенту платёж | opaque bearer session |
| `GET /api/v1/policies` | политика и текущие согласия | opaque bearer session |
| `POST /api/v1/consents/*` | privacy/marketing решения | opaque bearer session |

Зафиксированный `uvicorn==0.51.0` уже присутствует в runtime dependencies. Типизированная точка
сборки — `app.api.composition.create_api_application`: внешний composition root передаёт реальные
readiness, Redis, opaque session и YooKassa adapters. Production composition находится в
`app.api.__main__`; `AioHttpTransport` передаётся в `lifecycle_resources`, поэтому ASGI lifespan
открывает и закрывает его connection pool и затем закрывает Redis/DB. Импорт модуля сокеты не
открывает; запуск выполняется командой `python -m app.api`.

## Схема авторизации

```text
Telegram WebView
    -> HTTPS reverse proxy
    -> POST /api/v1/auth/telegram (X-Telegram-Init-Data: raw initData)
    -> HMAC-SHA-256 + constant-time compare
    -> auth_date TTL/future-skew validation
    -> Redis SET NX для keyed replay fingerprint
    -> shared SessionIssuer
    -> короткоживущая opaque server session
    -> последующие /api/v1 запросы с server session, не с bot token
```

Сервер принимает только строку `Telegram.WebApp.initData`. `initDataUnsafe`, Telegram username
и присланный frontend'ом user ID не являются источниками авторизации. Алгоритм следует
[официальной схеме Telegram Mini Apps](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):

1. Query string разбирается с запретом дубликатов и жёсткими лимитами размера/числа полей.
2. `hash` исключается, остальные декодированные `key=value` сортируются и объединяются `LF`.
   Новое поле `signature` остаётся в bot-token HMAC data-check-string; оно исключается только
   в отдельной third-party Ed25519-схеме.
3. Secret key вычисляется как HMAC-SHA-256 с ключом `WebAppData` и bot token как сообщением.
4. Ожидаемый hash сравнивается через `hmac.compare_digest`.
5. До успешной проверки не разбирается и не возвращается Telegram user ID.
6. `auth_date` ограничен коротким TTL (рекомендуется 300 секунд) и допустимым clock skew.
7. От принятого hash строится keyed fingerprint. Redis `SET ... NX EX` разрешает обмен только
   один раз; Redis outage закрывает авторизацию, а не отключает проверку.
8. Application service выпускает случайную opaque session с энтропией не менее 256 бит.
   В Redis/БД хранится только hash сессии, tenant/user binding, expiry и revoke state.

Один и тот же `initData` не предназначен для каждого API-запроса: он однократно обменивается
на server session. Bot token никогда не передаётся frontend'у и не используется как session key.

## Контракт `/api/v1`

DTO должны быть явными и не возвращать ORM-объекты. Поля времени передаются в ISO 8601 с
timezone, деньги — decimal string плюс ISO currency, IDs — серверные целые значения.

| Endpoint | Shared application service |
|---|---|
| `GET /api/v1/business` | business/settings query service |
| `GET /api/v1/services` | service catalog + feature guard |
| `GET /api/v1/masters` | booking/presentation services |
| `GET /api/v1/availability/dates` | lazy schedule projection / availability service |
| `GET /api/v1/availability/slots` | тот же availability service, что использует бот |
| `POST /api/v1/reservations` | reservation service + booking abuse policy |
| `POST /api/v1/appointments` | booking service с DB concurrency guarantees |
| `GET /api/v1/payments/{id}` | payment query/application service |
| `GET /api/v1/appointments` | appointment service с owner scope |
| `POST /api/v1/appointments/{id}/cancel` | appointment service |
| `POST /api/v1/appointments/{id}/reschedule` | reschedule service |
| `GET /api/v1/policies` | business presentation + versioned policy query |
| `POST /api/v1/consents/privacy` | versioned consent service |
| `POST /api/v1/consents/marketing` | versioned consent service |

HTTP controllers выполняют только decode, authentication/authorization context, вызов service,
safe error mapping и encode. Они не рассчитывают слоты, цены, скидки, права, переходы статусов
или payment state.

## Клиентский UX

Рекомендуемый frontend stack: TypeScript, React, Vite, TanStack Query и небольшой typed API
client. Telegram WebApp SDK оборачивается тонким adapter'ом; бизнес-состояние не хранится в
глобальном frontend store без необходимости. DTO валидируются на границе клиента, но серверная
валидация остаётся обязательной.

Поток записи:

1. Получить business branding, legal links и feature flags.
2. Показать категории и услуги. Выбранная услуга определяет допустимых мастеров.
3. В solo-режиме с одним bookable master пропустить экран выбора. В salon-режиме показать
   мастеров и «любой свободный мастер».
4. Календарь запрашивает только доступные даты в configured horizon, затем слоты выбранного дня;
   frontend не генерирует месяцы слотов заранее.
5. При выборе слота создать короткую reservation и показать обратный отсчёт.
6. Создать appointment тем же BookingService, который использует бот.
7. Если сервер вернул `409 slot_unavailable` или reservation expired, не повторять запись
   автоматически: удалить устаревший slot из UI, обновить день и предложить ближайшие варианты.
8. Если нужна предоплата, открыть только provider confirmation URL, полученный от backend.
   Успех показывать после server-side payment status/authoritative webhook, а не после redirect.

## YooKassa webhook boundary

Webhook body ограничен по размеру, требует `application/json`, запрещает повторяющиеся JSON keys
и никогда не логируется/не сохраняется целиком. Payload используется только как bounded envelope:
тип события и provider object ID. Присланным status, amount, currency и metadata доверять нельзя.

После разбора `YooKassaPaymentProvider.parse_webhook` реализованный lifecycle coordinator:

1. атомарно deduplicate событие по безопасному digest;
2. найти tenant-scoped payment/refund;
3. выполнить authenticated GET к YooKassa;
4. проверить provider ID, сумму, валюту и допустимый state transition через `PaymentService`;
5. сохранить только bounded projection/digest и вернуть `202` также для корректного дубля;
6. после `SUCCEEDED` вызвать общий `ReservationService.consume`, не меняя appointment напрямую.

Inbox/payment фиксируются до отдельной replay-safe транзакции поглощения резерва. Такой порядок
совпадает с lock order expiry worker (`reservation -> payment`) и не создаёт взаимную блокировку.
Если процесс упал между транзакциями, повторный webhook или expiry reconciliation завершит резерв.

Исходящие запросы выполняет `AioHttpTransport`: только HTTPS к exact allowlist host, без redirects,
proxy/environment trust и raw logging; connect/total timeout ограничены. `Content-Length` проверяется
до чтения, а фактические (в том числе decompressed) bytes — при потоковом чтении. Ответ обязан быть
конечным JSON object без дублирующихся keys.

Allowlist официальных YooKassa source networks настраивается на reverse proxy/firewall. Нельзя
доверять `X-Forwarded-For` без явно настроенной цепочки trusted proxies. Даже IP allowlist не
заменяет authoritative GET.

## Security boundaries

- `/api/*` доступен только по HTTPS. Reverse proxy обязан корректно и только от trusted peers
  передавать scheme в ASGI server.
- `Host` сравнивается с явным allowlist; wildcard запрещён.
- CORS отражает только точное значение известного HTTPS origin. Credentials mode не включён.
- CSP, HSTS, `nosniff`, `DENY`, no-referrer, no-store и restrictive Permissions Policy ставятся
  также на ошибках.
- Request headers ограничены по числу и суммарному размеру, неоднозначные дубликаты запрещены.
- JSON/body читается потоково с лимитом; заявленный чрезмерный `Content-Length` отклоняется до
  parsing.
- Network subject перед Redis rate limiter преобразуется keyed HMAC в opaque positive ID. IP,
  raw initData и Telegram ID не попадают в Redis key.
- Rate limiter и replay storage fail closed. `429`/`503` содержат только безопасный код и
  bounded `Retry-After`.
- Клиент получает generic error и correlation ID. Исключения, raw headers/body, bot token,
  session token и provider payload не добавляются в structured log context.

Нужны отдельные secrets: Telegram bot token, YooKassa key, session token pepper и
`API_RATE_LIMIT_SUBJECT_KEY`. Последний нельзя производить из публичного host или tenant slug.

## Composition port и настройки

Обязательные значения без небезопасных production defaults:

| Env/settings | Назначение |
|---|---|
| `BOT_TOKEN` | Telegram HMAC secret |
| `DATABASE_URL` | PostgreSQL async DSN |
| `REDIS_URL` | replay, rate limit и opaque sessions |
| `API_ALLOWED_HOSTS` | CSV exact Host allowlist |
| `MINI_APP_ALLOWED_ORIGINS` | CSV exact HTTPS origins |
| `API_RATE_LIMIT_SUBJECT_KEY` | отдельный HMAC secret, минимум 32 bytes |
| `API_SESSION_SIGNING_KEY` | отдельный session secret/pepper, минимум 32 bytes |
| `YOOKASSA_SHOP_ID` | optional provider account login |
| `YOOKASSA_SECRET_KEY` | optional provider Basic Auth secret |
| `YOOKASSA_BUSINESS_ID` | явный tenant scope webhook credentials |

YooKassa полностью опциональна. Если provider-значения не заданы, API работает без webhook.
Частичная конфигурация отклоняется до создания DB/Redis/network clients.

Bounded operational settings: `API_HOST=0.0.0.0`, `API_PORT=8080`,
`API_ENFORCE_HTTPS=true`, `API_MAX_BODY_BYTES=65536`,
`API_READINESS_TIMEOUT_SECONDS=3`, `TELEGRAM_INIT_DATA_TTL_SECONDS=300`,
`API_SESSION_TTL_SECONDS`, `YOOKASSA_WEBHOOK_RETENTION_DAYS=30`.

Исполняемый entrypoint — `python -m app.api`, порт по умолчанию `8080`, probes `/health/live` и
`/health/ready`. Программный composition port —
`create_api_application(ApiRuntimeOptions, ApiDependencies)`; обязательные lifecycle resources
передаются явно, а импорт модуля не открывает сокеты. Uvicorn принимает forwarded headers только
от loopback (`127.0.0.1`, `::1`); иной reverse-proxy topology требует явной доверенной настройки,
а не wildcard.

## Readiness

`/health/live` подтверждает только жизнеспособность процесса. `/health/ready` получает через
injected probe bounded boolean checks. Production composition должна включать как минимум DB,
Redis, обязательные workers и актуальность operational heartbeat; исключения и DSN не выходят в
ответ. Проверку backup freshness лучше публиковать отдельным operator-only monitoring check, а не
анонимному клиенту с подробными датами.

## План тестирования

- официальный/зафиксированный HMAC vector и property tests перестановки query fields;
- tamper, duplicate fields, invalid UTF-8/JSON/user ID, expired/future `auth_date`;
- параллельный Redis replay claim: ровно один победитель;
- CORS/Host/HTTPS matrix, включая preflight и wildcard rejection;
- body/header limits и chunked overflow;
- rate-limit allow/deny/outage и отсутствие raw subject в Redis key;
- generic 4xx/5xx и correlation propagation без secrets в response/log capture;
- webhook malformed/duplicate JSON, unsupported event, repeated notification;
- integration webhook: локальный state меняется только после authoritative provider response;
- API role/tenant matrix и конкурентный reservation/booking stale-slot сценарий;
- smoke tests за реальным reverse proxy с корректными trusted forwarded headers.

## План внешнего деплоя Mini App

1. Проверять `uvicorn==0.51.0`, dependency audit и lock/reproducible build.
2. Создать отдельные API rate/session secrets, exact hosts/origins и Redis namespace.
3. Запустить optional Compose profile `api`; session resolver и product routes уже подключены.
4. Настроить TLS, trusted proxy list, request/header limits и YooKassa network allowlist на proxy.
5. Выполнить security/integration/load smoke tests без публичного Mini App.
6. Настроить BotFather Mini App URL только после проверки HTTPS, CORS и incident rollback.

## Известные ограничения

- API-контейнер подключён как optional profile, но внешний reverse proxy/TLS не создаётся.
- Opaque session resolver и business endpoints реализованы; bearer не содержит Telegram ID.
- Product controllers вызывают существующие application services и не рассчитывают слоты/цены.
- YooKassa processor и bounded outbound transport реализованы; credentials, tenant binding и
  reverse-proxy IP allowlist всё ещё требуют deployment integration.
- Полноценный frontend Mini App в v0.4 foundation не создаётся.
