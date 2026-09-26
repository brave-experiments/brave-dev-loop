#!/bin/bash
# Remove this project's cron jobs -- the inverse of sync-schedules.sh.
#
# Strips the crontab block sync-schedules.sh installed for this project, in
# every marker spelling cron-blocks.sh knows, and leaves every other line alone:
# another project's block, and anything written by hand.
#
#   ./scripts/remove-schedules.sh            remove this project's block
#   ./scripts/remove-schedules.sh --print    print the crontab that would remain, touch nothing

set -e

PRINT_ONLY=false
if [ "${1:-}" = "--print" ]; then
  PRINT_ONLY=true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/cron-blocks.sh"

EXISTING=$(crontab -l 2>/dev/null || true)
CLEANED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" <<< "$EXISTING" | sed '/^$/N;/^\n$/d')

if $PRINT_ONLY; then
  printf '%s\n' "$CLEANED"
  exit 0
fi

if [ "$CLEANED" = "$(sed '/^$/N;/^\n$/d' <<< "$EXISTING")" ]; then
  echo "No cron jobs installed for $BOT_PROJECT_NAME; crontab left as it was."
  exit 0
fi

# An empty crontab is removed rather than installed as a blank file.
if [ -z "$(tr -d '[:space:]' <<< "$CLEANED")" ]; then
  crontab -r
else
  printf '%s\n' "$CLEANED" | crontab -
fi

echo "Cron jobs removed for $BOT_PROJECT_NAME."
echo "Run 'make schedules' to install them again, or 'crontab -l' for what remains."
