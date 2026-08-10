$ErrorActionPreference = "Stop"
python -m app.maintenance.backup_restore backup
exit $LASTEXITCODE
