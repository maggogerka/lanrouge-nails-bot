# Backup и проверка восстановления v0.4

## Гарантии и границы

`app.maintenance.backup_restore` создаёт PostgreSQL custom dump во временном файле, передаёт его
в зашифрованный restic repository и применяет retention. На POSIX временный файл принудительно
имеет mode `0600` и удаляется после операции. Production job рекомендуется запускать в Linux;
для ручного запуска в Windows отдельно проверьте NTFS ACL каталога `%TEMP%`.

Backup явно отключён, пока `BACKUP_ENABLED=true` не задан. Это возвращает структурированный
status `disabled`, а не создаёт незашифрованный локальный dump. Локальный/file restic repository
отклоняется: разрешены offsite backends `s3:`, `sftp:`, `rest:`, `b2:`, `azure:`, `gs:` и
`rclone:`. Локальный repository разрешается только CI при одновременных `APP_ENV=test` и
`BACKUP_ALLOW_LOCAL_REPOSITORY_FOR_TESTS=true`; production с таким исключением завершится ошибкой.

Требуются внешние binaries:

- `pg_dump`, `pg_restore`, `psql` совместимой с сервером PostgreSQL версии;
- `restic` и заранее инициализированный offsite repository;
- отдельный secret manager для DB/restic/backend credentials.

## Настройка backup job

Секреты не передавайте как CLI arguments и не сохраняйте в shell history. Передайте процессу
через secret manager или защищённый environment:

```text
BACKUP_ENABLED=true
DATABASE_URL=postgresql+asyncpg://<user>:<placeholder>@<host>:5432/<database>
DATABASE_PASSWORD_FILE=/run/secrets/postgres_password
RESTIC_REPOSITORY=s3:<bucket>/<prefix>
RESTIC_PASSWORD=<repository-encryption-password>
AWS_ACCESS_KEY_ID=<storage-key-id>
AWS_SECRET_ACCESS_KEY=<storage-secret>
BACKUP_KEEP_DAILY=7
BACKUP_KEEP_WEEKLY=5
BACKUP_KEEP_MONTHLY=12
BACKUP_MAX_AGE_HOURS=26
BACKUP_EXPECTED_DATABASE_NAME=<database>
```

Вместо прямого PostgreSQL-пароля поддерживаются `DATABASE_PASSWORD_FILE` и полный защищённый
`DATABASE_URL_FILE`; прямое значение URL и `DATABASE_URL_FILE` взаимоисключающие. Для restic
поддерживается `RESTIC_PASSWORD_FILE`. Файлы должны быть доступны только backup identity.
Runtime передаёт `pg_dump` только `PG*`, а restic — только системные и
backend/restic variables; `BOT_TOKEN` и полный `DATABASE_URL` дочерним процессам не передаются.

Запуск:

```bash
bash scripts/backup.sh
```

```powershell
.\scripts\backup.ps1
```

Успех: JSON с `status=succeeded` и exit code `0`. Configuration error возвращает `2`, runtime
failure — `1`. stdout/stderr внешних инструментов отбрасываются, чтобы случайно не сохранить
секрет; диагностика использует безопасный `error_code`. Failure hook получает тот же health
object и должен отправлять alert во внешний канал.

Рекомендуемый график: ежедневный backup, независимый alert при возрасте более 26 часов,
еженедельный `restic check` и ежемесячный restore drill. Retention не заменяет lifecycle/versioning
на стороне object storage и защиту bucket от удаления отдельной учётной записью.

Проверка свежести использует только метаданные последнего restic snapshot с тегом
`telegram-crm-postgres` и возвращает ненулевой exit code, если snapshot отсутствует, повреждён
или старше `BACKUP_MAX_AGE_HOURS`:

```bash
bash scripts/backup-freshness.sh
```

## Автоматический запуск на Ubuntu через systemd

В `deploy/systemd/` лежат шаблоны ежедневного backup, ежечасной проверки свежести и ежемесячного
restore-test. Все production jobs используют `--require-enabled`: выключенный backup считается
ошибкой, а не успешным заданием. `psql` также проверяет наличие `public.alembic_version` перед
`pg_dump`, поэтому пустая/чужая БД не создаёт ложный «успешный» snapshot.

Установите защищённый environment-файл. В нём находятся только точное имя Compose-проекта и
пути; сами пароли остаются в отдельных secret files:

```bash
sudo install -d -o root -g root -m 0700 /etc/telegram-crm /etc/telegram-crm/secrets
sudo install -o root -g root -m 0600 deploy/systemd/backup.env.example /etc/telegram-crm/backup.env
sudoedit /etc/telegram-crm/backup.env
```

Обязательно задайте уникальный `COMPOSE_PROJECT_NAME`, абсолютный `ENV_FILE` и абсолютные
`POSTGRES_PASSWORD_SECRET_FILE`/`RESTIC_PASSWORD_SECRET_FILE`. В application `.env` задайте
`APP_ENV=production`, `BACKUP_ENABLED=true` и точный `BACKUP_EXPECTED_DATABASE_NAME`. Не используйте
project name другого клиента. Если checkout находится не в `/opt/telegram-crm-bot`, сначала
исправьте `WorkingDirectory` во всех трёх service templates.

Установите units:

```bash
sudo install -m 0644 deploy/systemd/telegram-crm-backup*.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/telegram-crm-backup*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-crm-backup.timer telegram-crm-backup-freshness.timer telegram-crm-backup-restore-test.timer
sudo systemctl list-timers 'telegram-crm-backup*'
```

`Persistent=true` запускает пропущенную задачу после включения VPS. Сначала выполните обе services
вручную и убедитесь, что exit code успешный:

```bash
sudo systemctl start telegram-crm-backup.service
sudo systemctl start telegram-crm-backup-freshness.service
sudo systemctl status telegram-crm-backup.service telegram-crm-backup-freshness.service
sudo journalctl -u telegram-crm-backup.service -u telegram-crm-backup-freshness.service --since today
```

Каждый service содержит `OnFailure=telegram-crm-backup-alert@%n.service`. Установите проверяемый
root-owned executable `/usr/local/sbin/telegram-crm-backup-alert`, который принимает имя unit
первым аргументом и отправляет событие во внешний мониторинг без содержимого environment/logs.
До установки hook не включайте timers: иначе ошибка backup будет видна в systemd, но уведомление
владельцу не уйдёт. Не помещайте секреты в `ExecStart`, unit или alert payload.

При обновлении со старых unit names сначала установите и вручную проверьте новые units, затем:

```bash
sudo systemctl disable --now lanrouge-backup.timer lanrouge-backup-freshness.timer
sudo rm -f /etc/systemd/system/lanrouge-backup.service /etc/systemd/system/lanrouge-backup.timer
sudo rm -f /etc/systemd/system/lanrouge-backup-freshness.service /etc/systemd/system/lanrouge-backup-freshness.timer
sudo systemctl daemon-reload
```

Эта миграция не затрагивает Compose volumes, PostgreSQL или restic snapshots.

Дополнительный обязательный регламент:

- еженедельно запускайте `restic check` из того же защищённого backup-окружения и проверяйте его
  ненулевой exit code; команда проверяет целостность repository, но не заменяет восстановление;
- ежемесячно создавайте отдельную пустую test/restore БД и выполняйте описанный ниже
  `restore-test`; фиксируйте дату, RPO/RTO и контрольные количества строк без персональных данных;
- после ротации DB/restic/S3-секретов немедленно выполняйте backup, freshness check и restore drill.

## Guarded restore drill

Core никогда не создаёт и не удаляет базы. Оператор заранее создаёт отдельную пустую БД с именем,
содержащим `restore` или `test`, и отдельным least-privilege пользователем. Production database,
`postgres`, `template0` и `template1` отклоняются.

```text
RESTORE_DATABASE_URL=postgresql+asyncpg://<restore-user>:<password>@<host>:5432/app_restore_test
RESTORE_DATABASE_PASSWORD_FILE=/run/secrets/restore_postgres_password
RESTORE_ACKNOWLEDGE=RESTORE_TO_SEPARATE_TEST_DATABASE
```

После ручной проверки target URL:

```bash
bash scripts/restore-test.sh
```

```powershell
.\scripts\restore-test.ps1
```

Последовательность операции:

1. `restic dump latest` скачивает tagged dump во временный защищённый файл.
2. `pg_restore --list` проверяет custom archive.
3. `pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error` обновляет только
   указанную restore/test DB.
4. `psql ... SELECT 1` проверяет доступность восстановленной БД.

Затем вручную запустите `alembic current`, read-only smoke tests и сверку количества критичных
tenant/payment/appointment строк. Не направляйте Telegram bot или production workers на restore DB.
Зафиксируйте время восстановления и фактические RPO/RTO без записи персональных данных.

## Важные ограничения

- Redis FSM/ephemeral rate-limit keys в PostgreSQL dump не входят и должны считаться
  восстановимым transient state.
- Telegram-hosted files не копируются; сохраняются только разрешённые file identifiers.
- Restic password нельзя потерять: без него snapshot криптографически невосстановим.
- Backup считается проверенным только после успешного restore drill, а не после exit code `0`
  команды upload.
- CI выполняет настоящий локальный encrypted backup → restore smoke, но он не заменяет проверку
  production offsite credentials, retention, alerting и восстановления на VPS покупателя.
