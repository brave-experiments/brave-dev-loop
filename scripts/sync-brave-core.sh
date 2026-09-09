#!/bin/bash
# Deprecated: renamed to sync-target-repo.sh.
#
# Kept as a shim because the cron jobs installed by sync-schedules.sh hard-reset
# the bot repo to origin before running. Without this, every scheduled job on
# every deployment would break the moment the rename landed and stay broken
# until the operator re-ran `make schedules`. Safe to delete once every
# deployment has re-run it.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sync-target-repo.sh" "$@"
