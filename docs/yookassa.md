# Подключение YooKassa

До production нужны отдельный YooKassa-аккаунт бизнеса, shop ID, secret key, публичный HTTPS
домен и reverse proxy. Деньги идут напрямую бизнесу; платформа не хранит ключ в БД и не принимает
выручку на свой счёт.

## Настройки

```dotenv
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...
YOOKASSA_BUSINESS_ID=1
YOOKASSA_RETURN_URL=https://t.me/your_bot
YOOKASSA_WEBHOOK_RETENTION_DAYS=30
```

В production предпочтительны `YOOKASSA_SHOP_ID_FILE` и `YOOKASSA_SECRET_KEY_FILE` из secret
manager. Не задавайте ключ через Telegram-чат. Если YooKassa не используется, оставьте все
provider-поля пустыми и держите feature flag `yookassa_payments=false`.

## Webhook

Endpoint: `POST /api/v1/webhooks/yookassa`. Он должен быть доступен только через HTTPS.
Reverse proxy ограничивает размер body, доверенные proxy headers и, после сверки с актуальной
документацией провайдера, source networks. Приложение дополнительно ограничивает body, запрещает
duplicate JSON keys и не пишет raw payload/headers в логи.

Webhook используется как сигнал. Coordinator выполняет authenticated GET к YooKassa и сверяет
provider ID, сумму, валюту, business/account metadata и допустимый transition. Ошибка или чужой
ID не подтверждают запись. Повтор события возвращает безопасный `202` без дубля перехода.

## Включение

1. Настройте sandbox credentials и HTTPS callback.
2. Запустите API profile и проверьте `/health/ready`.
3. Проведите тесты success/cancel/wrong amount/wrong currency/replay/refund.
4. В «Функции бота» включите «Предоплата» и «YooKassa».
5. В «Оплата» явно переключите режим на YooKassa.
6. Только после sandbox sign-off замените secrets на live и повторите smoke test.

При недоступности YooKassa не подтверждайте платежи вручную по screenshot. Оставьте intent в
ожидающем состоянии, проверьте provider dashboard/API и следуйте `incident-response.md`.
