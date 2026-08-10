# Ротация секретов

## Общие правила

- Храните production secrets в secret manager или защищённых runtime secrets, не в Git, `.env`
  backup, issue, chat или shell history.
- Используйте отдельные identities для приложения, миграций, backup и restore drill.
- Ротируйте один credential за раз, проверяйте health, затем отзывайте старый. Для credential без
  overlap заранее планируйте короткое maintenance window.
- Никогда не прикладывайте raw exception, webhook, provider response или `docker compose config`
  к инциденту без redaction.

## Telegram bot token

1. Объявите maintenance window и остановите polling старой версии.
2. Выпустите/отзовите token через BotFather.
3. Обновите secret manager и пересоздайте только bot/workers, которым нужен token.
4. Проверьте `/start`, staff RBAC и доставку одной тестовой нотификации.
5. Убедитесь, что старый token больше не работает и отсутствует в логах/Sentry.

При подозрении на утечку сначала отзывайте token, затем расследуйте; не ждите завершения анализа.

## PostgreSQL и Redis

Для PostgreSQL предпочтительна overlap-ротация: создать новый login с минимальными grants,
обновить `DATABASE_URL`, проверить connections/migrations/read-write smoke test, после чего
отозвать старый login. Если меняется пароль существующей роли, требуется согласованный restart
всех процессов. Backup и restore identities ротируются отдельно.

Для Redis используйте ACL user с минимальными командами и key prefix. Добавьте новый пароль/ACL,
переключите приложения, проверьте FSM, rate limiter и heartbeat, затем удалите старый credential.
Не публикуйте PostgreSQL/Redis ports в интернет.

## Payment provider и webhook

1. Создайте новый provider API credential с минимальным scope.
2. Разверните его через secret manager; provider account reference в БД не является credential.
3. Выполните безопасный test payment/refund и authoritative status refresh.
4. Если provider поддерживает webhook signing secrets с overlap, принимайте старый и новый только
   на ограниченный период, затем отзовите старый.
5. Проверьте dedupe, signature rejection и отсутствие raw payload/Authorization в логах/Sentry.

При компрометации временно отключите online payments на уровне business policy, но не помечайте
непроверенные платежи успешными вручную. Сверьте статусы через authoritative provider API.

## Restic и object storage

Object-storage access key и restic repository encryption key — разные секреты.

- Storage key: создайте новый restricted key, обновите backup job, выполните backup/check/restore
  drill, затем отзовите старый.
- Restic password: используйте `restic key add`, проверьте доступ новым ключом и только затем
  `restic key remove` для старого. Простая замена `RESTIC_PASSWORD` без добавления ключа сделает
  существующие snapshots недоступными.
- Включите bucket versioning/object lock по требованиям бизнеса и отдельный delete credential.

## Sentry и прочие API keys

Обновите DSN/API key в secret manager, пересоздайте затронутые процессы, отправьте scrubbed test
event и отзовите старый key. Даже если DSN считается публичным идентификатором проекта, не
помещайте его в логи и пользовательские ответы.

## Завершение ротации

Зафиксируйте только дату, владельца, тип секрета, затронутые компоненты и результат smoke test.
Не записывайте старое/новое значение или последние символы секрета. Проверьте heartbeat,
dependency health, backup status и отсутствие authentication spikes минимум один рабочий цикл.
