# Мониторинг и observability v0.4

## Что уже предоставляет приложение

- `app.logging.JsonFormatter` пишет однострочный JSON и удаляет credentials из URL,
  `Authorization`, API keys, cookies, Telegram `initData`, webhook/payment payloads,
  card-like значения и секреты из exception text.
- `app.observability.init_sentry` включает Sentry только при непустом DSN, всегда передаёт
  `send_default_pii=False` и устанавливает строгий `before_send` scrubber. При заданном DSN
  отсутствие `sentry-sdk` считается ошибкой конфигурации, а не тихим отключением.
- `app.component_health.ComponentHealthMonitor` хранит bounded heartbeat в Redis, использует
  серверное время Redis и возвращает структурированный статус `healthy`, `overdue`, `missing`
  или `store_unavailable`.

Runtime wiring уже подключён для `bot`, reminders, broadcasts, reference cleanup и
reservation expiry. Каждый процесс инициализирует observability до запуска event loop и
публикует heartbeat только после успешной работы. Compose задаёт отдельный
`HEALTHCHECK_COMPONENT` для каждого постоянного процесса, поэтому остановка опционального
worker не делает нездоровыми остальные контейнеры. API использует отдельный dependency probe
`GET /health/ready`.

## Рекомендуемые heartbeat-политики

| Компонент | Максимальный возраст | Обязательный |
| --- | ---: | --- |
| `bot` | 60 секунд | да |
| `reminders` | 2 × poll interval + 30 секунд | да |
| `broadcasts` | 2 × poll interval + 30 секунд | да |
| `reference_cleanup` | cleanup interval + 30 минут | да |
| `reservation_expiry` | 2 × poll interval + 30 секунд | да при включённых платежах |
| `backup` | 26 часов | да в production |

TTL heartbeat автоматически равен как минимум трём допустимым возрастам. Поэтому исчезнувший
ключ означает `missing`, а не бесконечно старую запись. Ключ имеет безопасную форму
`<REDIS_NAMESPACE>:<INSTANCE_ID>:heartbeat:<component>`, а значение содержит только серверное
время Redis. Telegram ID, payload и персональные данные в heartbeat не попадают.

## Sentry

`sentry-sdk` зафиксирован в production lock-файле, но отправка событий остаётся опциональной и
включается только непустым `SENTRY_DSN`. Каждый долгоживущий процесс вызывает общий wrapper:

```python
initialize_observability(settings)
```

Wrapper передаёт environment и release `v0.4.0`; если настроенный SDK нельзя безопасно
инициализировать, процесс не запускает event loop и завершается с кодом 2.

Не добавляйте `send_default_pii=True`, request bodies или raw webhook payload как attachment.
События должны содержать correlation ID, безопасный error code, component и release, но не
Telegram username/phone, payment provider object ID, URL подтверждения или секреты.

## Алерты

Минимальный production-набор:

- любой required heartbeat `overdue/missing` два последовательных check-интервала;
- heartbeat store `store_unavailable` более 60 секунд;
- backup status не `succeeded` более 26 часов;
- рост ошибок Telegram/provider/DB, payment webhook backlog и reservation expiry backlog;
- заполнение диска выше 80%, PostgreSQL connection saturation и Redis eviction;
- Sentry regression после нового release.

Failure hook получает только структурированный snapshot без exception text. Подключайте к нему
внешний PagerDuty/Telegram/Sentry adapter с rate limit и dedupe; ошибка alert backend не должна
ломать health check.

## Проверка перед production

1. Намеренно остановить один test worker и убедиться, что статус меняется на `overdue`, затем
   `missing`, а alert восстанавливается после heartbeat.
2. Отключить test Redis и проверить `heartbeat_store_unavailable`.
3. Отправить тестовое исключение с искусственными Authorization/cookie/card значениями и
   убедиться, что ни JSON logs, ни Sentry не содержат исходные значения.
4. Проверить correlation ID между handler, worker и provider-safe error event.

Внешние ограничения: нужны Redis высокой доступности, Sentry/другой error backend, сборщик JSON
логов и независимый alert channel. Сам репозиторий не предоставляет hosted monitoring.
