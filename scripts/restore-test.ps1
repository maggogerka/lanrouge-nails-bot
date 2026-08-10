$ErrorActionPreference = "Stop"
python -m app.maintenance.backup_restore restore-test
exit $LASTEXITCODE
