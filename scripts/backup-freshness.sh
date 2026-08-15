#!/usr/bin/env sh
set -eu

python -m app.maintenance.backup_restore check-freshness --require-enabled
