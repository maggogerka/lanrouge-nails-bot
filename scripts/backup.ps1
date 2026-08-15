$ErrorActionPreference = "Stop"
python -m app.maintenance.backup_restore backup --require-enabled
exit $LASTEXITCODE
