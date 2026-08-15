$ErrorActionPreference = "Stop"
python -m app.maintenance.backup_restore check-freshness --require-enabled
exit $LASTEXITCODE
