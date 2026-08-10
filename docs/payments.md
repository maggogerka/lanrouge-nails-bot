# Платежи и резервы v0.4.0

Клиентская оплата услуги полностью отделена от CRM-подписки бизнеса. Таблицы `payments`,
`refunds`, `payment_webhook_events` и `booking_reservations` всегда scoped по `business_id`.
Суммы хранятся как `NUMERIC`, валюта — ISO-код; карточных реквизитов в схеме нет.

## Режимы

- `DISABLED`: слот повторно проверяется в транзакции, запись сразу получает `CONFIRMED`.
- `MANUAL`: создаются `PENDING_MANUAL_CONFIRMATION`, активный резерв и локальный платёж.
  Клиент видит только утверждённую владельцем инструкцию. Сотрудник с правом управления
  платежами явно подтверждает, что деньги реально получены; действие попадает в audit.
- `YOOKASSA`: создаются `PENDING_PAYMENT`, резерв и локальный intent. Внешний запрос выполняется
  вне DB-транзакции с тем же idempotency key; результат затем сверяется и сохраняется под lock.

Pending-статусы занимают время мастера. Worker `python -m app.workers.reservation_expiry`
claim'ит просроченные резервы через `FOR UPDATE SKIP LOCKED`, переводит запись в
`PAYMENT_EXPIRED` и освобождает слот. Повторный цикл безопасен.

## Идемпотентность и конкурентность

- уникальны `(business_id, idempotency_key)` и provider payment/refund IDs;
- API требует `Idempotency-Key`, Telegram FSM сохраняет ключ и reservation token до ответа;
- повтор точно того же checkout возвращает существующий результат;
- reuse ключа с другой суммой/записью отклоняется;
- PostgreSQL exclusion constraint запрещает пересекающиеся активные записи одного мастера;
- webhook дедуплицируется до обработки и никогда не является доказательством оплаты сам по себе.

## Возвраты

Полный и частичный возврат проходят через `RefundCoordinator` и provider abstraction. Перед
операцией сотрудник видит сумму и даёт явное подтверждение. Pending refund переводит запись в
`REFUND_PENDING`; проверенный результат — в `PARTIALLY_REFUNDED` или `REFUNDED`. Повтор команды
с тем же ключом не увеличивает возвращённую сумму.

Политика отмены (`cancellation_refund_deadline_hours`, процент позднего возврата) хранится в
`business_payment_settings`. Формулировка «предоплата всегда невозвратная» не используется.

## Безопасность

- запрещено просить CVV/CVC, срок карты, SMS-код или сохранять provider payload;
- secrets поступают только через env/file secrets и маскируются в repr/log/Sentry;
- confirmation URL принимается только из проверенного HTTPS provider response;
- manual-инструкция ограничена по длине и отклоняет карточные секреты;
- сумма, валюта, account metadata и provider ID сверяются сервером.
