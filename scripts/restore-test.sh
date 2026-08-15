#!/usr/bin/env sh
set -eu

python -m app.maintenance.backup_restore restore-test --require-enabled
