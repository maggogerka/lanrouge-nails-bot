param(
    [switch]$Yes,
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    docker compose config --quiet
    if (-not $Yes) {
        $confirmation = Read-Host "Будут удалены локальные PostgreSQL и Redis. Введите DELETE"
        if ($confirmation -cne "DELETE") {
            Write-Host "Очистка отменена."
            exit 1
        }
    }

    if (-not $SkipBackup) {
        $postgresContainer = (docker compose ps -q postgres).Trim()
        if (-not $postgresContainer) {
            throw "PostgreSQL-контейнер не запущен. Запустите docker compose up -d postgres."
        }
        $backupDirectory = Join-Path $projectRoot ".secrets\backups"
        New-Item -ItemType Directory -Force $backupDirectory | Out-Null
        $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $containerBackup = "/tmp/pre-reset-$timestamp.dump"
        $hostBackup = Join-Path $backupDirectory "pre-reset-$timestamp.dump"
        docker exec $postgresContainer sh -c "pg_dump -U `$POSTGRES_USER -d `$POSTGRES_DB -Fc -f $containerBackup"
        if ($LASTEXITCODE -ne 0) { throw "Не удалось создать резервную копию PostgreSQL." }
        docker cp "${postgresContainer}:${containerBackup}" $hostBackup
        if ($LASTEXITCODE -ne 0) { throw "Не удалось скопировать резервную копию на компьютер." }
        Write-Host "Резервная копия: $hostBackup"
    }

    docker compose down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Не удалось удалить локальные Docker volumes." }
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) { throw "Не удалось запустить чистый проект." }
    docker compose ps
    Write-Host "Локальная БД и Redis очищены. Миграции и bootstrap владельца запустятся автоматически."
}
finally {
    Pop-Location
}
