# Incident response

## Приоритеты

1. Защитить клиентов, платежи и доступ к бизнес-данным.
2. Остановить дальнейшую компрометацию с минимальной потерей доказательств.
3. Восстановить сервис из проверенного состояния.
4. Выполнить обязательные уведомления и устранить первопричину.

Severity `SEV-1`: подтверждённая утечка credentials/персональных или платёжных данных, неверное
подтверждение платежей, уничтожение/шифрование production DB, захват Telegram bot. `SEV-2`:
недоступность booking/payment/notifications, просроченный backup или массовые ошибки без
подтверждённой утечки.

## Первые 15 минут

- Назначьте incident commander и безопасный out-of-band канал.
- Зафиксируйте UTC-время, release, компоненты и безопасные correlation/error codes.
- При утечке bot/provider/storage credential немедленно отзовите его по runbook ротации.
- При ошибочных payment/webhook transitions отключите online payment feature, сохраните webhook
  dedupe metadata и выполняйте сверку только через authoritative provider API.
- При подозрении на запись в БД остановите mutating workers/bot, оставив read-only monitoring,
  если это безопасно. Не запускайте автоматический downgrade или restore поверх production.
- Не публикуйте raw logs, DB dump, Telegram update, webhook body, Authorization/cookie/initData.

## Сбор доказательств

Сохраните immutable копии scrubbed JSON logs, deploy commit/image digest, migration revision,
health snapshots, provider event IDs в хешированном/редактированном виде и audit records.
Ограничьте доступ и ведите chain of custody. Не запускайте очистку логов/volumes и destructive
команды. Sentry event сначала проверьте на PII, даже при включённом scrubber.

## Сценарии

### Telegram token или staff account

Отзовите token/session, остановите polling, проверьте изменения staff membership/roles и audit,
ротируйте token, затем запустите минимальный RBAC smoke test. Не восстанавливайте административный
доступ только по Telegram username или сообщению в чате.

### Payment/webhook anomaly

Остановите новые online payment intents, но не удаляйте payments/refunds/webhook inbox. Сверьте
amount/currency/business/appointment/provider IDs через provider dashboard/API. Повторные webhook
должны проходить dedupe; raw body используется только в памяти для signature verification и не
сохраняется в ticket. Refund выполняется идемпотентно после row lock и проверки pending суммы.

### Database compromise или потеря данных

Изолируйте writer credentials и сделайте forensic snapshot инфраструктурным способом. Выберите
последний проверенный restic snapshot, восстановите только в отдельную `restore/test` DB через
`docs/backup-restore.md`, выполните integrity/read-only smoke checks. Переключение production
требует отдельного одобрения incident commander и документированного RPO/RTO; core restore CLI
намеренно не умеет восстанавливать поверх production.

### Backup repository compromise

Отзовите storage key, включите object lock/legal hold при наличии, проверьте repository с
независимой identity. Не удаляйте подозрительные snapshots до сохранения доказательств. Ротируйте
storage и restic keys в правильной последовательности и подтвердите новый restore drill.

## Восстановление сервиса

Возвращайте компоненты по одному: dependencies → migrations/readiness → bot в ограниченном режиме
→ payment/reservation expiry → notifications/broadcasts. Для каждого проверьте heartbeat,
backlog, error rate и tenant isolation. Нельзя считать инцидент закрытым только потому, что
health endpoint отвечает `ok`.

## Коммуникации и завершение

Юридический/профильный специалист определяет необходимость и сроки уведомления субъектов данных,
регуляторов, банка/provider и страховщика. Сообщения должны быть точными и не раскрывать новые
секреты или неподтверждённые причины.

После стабилизации подготовьте blameless postmortem: timeline UTC, impact, root cause,
contributing controls, detection gap, фактические RPO/RTO и конкретные owners/deadlines. Добавьте
регрессионный тест/alert, проведите ротацию затронутых secrets и повторный restore/incident drill.
