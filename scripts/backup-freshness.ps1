$ErrorActionPreference = "Stop"
python -m app.maintenance.backup_restore check-freshness
exit $LASTEXITCODE
