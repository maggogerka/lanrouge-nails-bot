# YooKassa: полное руководство для разработчика

Это техническая инструкция для одного экземпляра CRM. Короткую инструкцию, которую можно
отправить владельцу бизнеса, смотрите в [YOOKASSA_CLIENT_GUIDE.md](../YOOKASSA_CLIENT_GUIDE.md).
Деньги поступают непосредственно магазину бизнеса в YooKassa; CRM не должна принимать выручку
на счёт разработчика и не хранит реквизиты банковских карт.

## 1. Важная граница перед включением

Текущая интеграция создаёт одностадийный платеж (`capture=true`) с redirect-подтверждением,
проверяет его через authenticated API, принимает уведомления и поддерживает возвраты. Она **не
формирует объект `receipt`**. До live-запуска владелец и бухгалтер обязаны определить схему чеков
и 54-ФЗ. Если настройки настоящего магазина требуют передавать чек в запросе API, YooKassa-режим
в этой версии включать нельзя: сначала нужен отдельный модуль фискальных позиций, НДС и чеков.

Официальные материалы:

- [формат API и HTTP Basic Auth](https://yookassa.ru/developers/using-api/interaction-format);
- [входящие уведомления](https://yookassa.ru/developers/using-api/webhooks);
- [тестовый режим](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing);
- [решения для 54-ФЗ](https://yookassa.ru/developers/payment-acceptance/receipts/54fz/basics).

## 2. Что запросить у владельца

Предпочтительный вариант — владелец приглашает ваш отдельный аккаунт с ролью «Разработчик», а
не пересылает пароль от кабинета. Для test и live магазинов нужны разные `shopId` и secret key.
Secret key передаётся только через согласованный secret manager или добавляется владельцем прямо
на VPS; Telegram, email, issue и git для этого не подходят.

Также получите:

- подтверждённое решение по онлайн-кассе, НДС, предмету и способу расчёта;
- домен для HTTPS webhook и доступ к DNS/reverse proxy;
- username бота для `return_url`;
- письменное разрешение на тестовый платёж и возврат;
- контакт ответственного за сверку платежей и инциденты.

## 3. Сначала только тестовый магазин

1. В кабинете YooKassa создайте тестовый магазин.
2. Возьмите его `shopId` и test secret key.
3. Подготовьте отдельный URL, например
   `https://pay.example.com/api/v1/webhooks/yookassa`. Не смешивайте test webhook с live.
4. В кабинете «Интеграция → HTTP-уведомления» укажите URL и включите поддерживаемые события:
   `payment.waiting_for_capture`, `payment.succeeded`, `payment.canceled`, `refund.succeeded`.
5. URL должен работать по HTTPS на порту 443 или 8443 с TLS 1.2+.

Webhook является только недоверенным сигналом. CRM не принимает сумму или статус из его body как
доказательство: после дедупликации выполняется authoritative GET к YooKassa, затем повторно
проверяются provider/payment ID, business metadata, сумма и валюта.

## 4. HTTPS и reverse proxy

API запускается профилем `api`, слушает `api:8080` внутри Docker и не публикует PostgreSQL/Redis.
Reverse proxy должен:

- завершать TLS и перенаправлять только HTTPS на `api:8080`;
- сохранять `Host: pay.example.com`;
- передавать `X-Forwarded-Proto: https`;
- ограничить request body (приложение дополнительно ограничивает его 64 KiB);
- иметь стабильный внутренний IP, добавленный в `API_TRUSTED_PROXY_IPS`;
- не писать Authorization, тело webhook и ответы провайдера в access/error logs.

Не используйте `API_TRUSTED_PROXY_IPS=*` и не добавляйте туда целую недоверенную сеть. По умолчанию
доверен только loopback. Для proxy-контейнера назначьте статический адрес в отдельном Docker
override и укажите его точно, например `API_TRUSTED_PROXY_IPS=172.20.0.10`.

Минимальные API-настройки:

```dotenv
APP_ENV=production
API_ALLOWED_HOSTS=pay.example.com
API_TRUSTED_PROXY_IPS=172.20.0.10
MINI_APP_ALLOWED_ORIGINS=https://pay.example.com
API_ENFORCE_HTTPS=true
API_RATE_LIMIT_SUBJECT_KEY=<случайный секрет не короче 32 байт>
API_SESSION_SIGNING_KEY=<другой случайный секрет не короче 32 байт>
YOOKASSA_BUSINESS_ID=1
YOOKASSA_RETURN_URL=https://t.me/your_bot_username
YOOKASSA_WEBHOOK_RETENTION_DAYS=30
# Только после документально подтверждённой внешней фискализации:
YOOKASSA_FISCALIZATION_MODE=external
```

`API_RATE_LIMIT_SUBJECT_KEY` и `API_SESSION_SIGNING_KEY` должны быть разными. Для одного
экземпляра CRM `YOOKASSA_BUSINESS_ID` обычно равен `1`; перед production проверьте фактический
tenant в БД.

## 5. Секреты YooKassa

Рекомендуется file-secret режим. Скопируйте `compose.yookassa.yml.example` в неотслеживаемый
`compose.yookassa.yml`, создайте два файла без перевода строки и ограничьте доступ:

```bash
install -d -m 0700 .secrets
printf '%s' '<test-shop-id>' > .secrets/yookassa_shop_id
printf '%s' '<test-secret-key>' > .secrets/yookassa_secret_key
chmod 0644 .secrets/yookassa_shop_id .secrets/yookassa_secret_key
```

Application-контейнеры работают как UID `10001`. Compose file secrets должны быть читаемы этим
UID, поэтому файлы имеют mode `0644`, но защищены родительским каталогом `.secrets` с mode `0700`.
Не делайте каталог доступным другим host users. Внешний secret manager может вместо этого
выдать файлы UID/GID `10001` с более строгим mode.

Не вводите реальные значения в команды, если shell history доступна другим пользователям:
безопаснее открыть файлы через защищённый редактор или secret manager. В `.env` прямые
`YOOKASSA_SHOP_ID` и `YOOKASSA_SECRET_KEY` оставьте пустыми. Проверяйте Compose без публикации
его вывода — итоговый config может содержать чувствительные env-значения других компонентов.

Запуск:

```bash
docker compose -f docker-compose.yml -f compose.production.yml -f compose.profiles.yml -f compose.yookassa.yml --profile api config --quiet
docker compose -f docker-compose.yml -f compose.production.yml -f compose.profiles.yml -f compose.yookassa.yml --profile api up -d --build bot api
docker compose -f docker-compose.yml -f compose.production.yml -f compose.profiles.yml -f compose.yookassa.yml --profile api ps
```

Проверьте через публичный домен `GET /health/live` и `GET /health/ready`. Оба запроса должны идти
через тот же proxy и возвращать 200. Сам `/api/v1/webhooks/yookassa` принимает только `POST`.

Override передаёт `YOOKASSA_*_FILE` одновременно `bot` и `api`: API принимает webhook, а bot
создаёт клиентскую оплату. Если ключи настроены частично либо production не содержит явного
`YOOKASSA_FISCALIZATION_MODE=external`, YooKassa остаётся недоступной; ручная предоплата работает.
Значение `external` разрешено ставить только после проверки внешней кассы/фискализатора: текущий
provider намеренно не формирует налоговый объект `receipt`.

## 6. Включение в боте

Только после успешного test-контура:

1. `/admin` → «🧩 Функции бота» → включите «Предоплата» и «YooKassa».
2. `/admin` → «💳 Предоплаты» → настройки → выберите «Режим: YooKassa» и подтвердите.
3. Укажите предоплату у тестовой услуги, создайте совместимые рабочее место, мастера и окно.
4. С отдельного клиентского аккаунта оформите запись и перейдите по кнопке оплаты.

## 7. Обязательный test checklist

- успешная оплата подтверждает ровно одну запись;
- отменённая/неоконченная оплата не подтверждает запись, а просроченный резерв освобождает worker;
- повтор одного webhook не создаёт второй платёж и не увеличивает возврат;
- webhook с чужим ID, суммой, валютой или business metadata отклоняется;
- перезапуск API между ответом YooKassa и локальным commit безопасно восстанавливается повтором;
- полный и частичный возврат проверены с одним и тем же idempotency key;
- недоступность YooKassa не держит PostgreSQL row lock и оставляет безопасный повторяемый статус;
- в `docker compose logs api reservation-worker` нет ключей, URL подтверждения, webhook body,
  телефонов и карточных данных;
- резерв действительно освобождается работающим базовым `reservation-worker`.

Используйте только официальные тестовые реквизиты из документации YooKassa. Настоящую карту в
test-магазине применять нельзя.

## 8. Переход в live

1. Получите письменное подтверждение владельца по договору, тарифам, возвратам и 54-ФЗ.
2. Сохраните backup и зафиксируйте текущую конфигурацию без значений секретов.
3. Остановите API, замените оба file secrets на `shopId` и key настоящего магазина.
4. В live-кабинете отдельно настройте тот же HTTPS webhook и события.
5. Перезапустите API и проверьте readiness.
6. Выполните одну разрешённую оплату на минимальную реальную сумму, сверку в кабинете и возврат.
7. Только затем открывайте YooKassa-режим клиентам.

## 9. Ротация, сбой и откат

- При утечке немедленно выключите YooKassa feature, отзовите ключ в кабинете, выпустите новый,
  замените secret file и пересоздайте только API.
- Не подтверждайте платёж по скриншоту. Сверяйте его в кабинете/API; сохраняйте локальные
  payment/refund/webhook строки для расследования.
- Для быстрого отката переключите новые записи на ручной режим или режим без предоплаты; уже
  созданные платежи не удаляйте.
- После смены ключа повторите success/cancel/refund/replay smoke tests.
- Следуйте [incident-response.md](incident-response.md) и [secrets-rotation.md](secrets-rotation.md).

## 10. Остаточный риск env_file

Общий Compose передаёт выбранный `ENV_FILE` application-контейнерам для совместимости существующей
конфигурации. Поэтому прямой `YOOKASSA_SECRET_KEY` в `.env` увидят процессы, которым он не нужен.
File-secret override выше не помещает сам ключ в `.env`, но путь и другие общие настройки всё ещё
передаются. Полное разделение env-файлов каждого worker потребует отдельного deployment-hardening
этапа; до него ограничьте доступ к Docker socket, VPS и `.env` и не запускайте посторонние процессы
под deployment-пользователем.
