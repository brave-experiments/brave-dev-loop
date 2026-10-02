#!/bin/bash
# Remove this project's cron jobs -- the inverse of sync-schedules.sh.
#
# Strips the crontab blocks sync-schedules.sh installed for this project, in
# every marker spelling cron-blocks.sh knows, and leaves every other line alone:
# another project's block, and anything written by hand.
#
#   ./scripts/remove-schedules.sh                    remove every group's block
#   ./scripts/remove-schedules.sh --group review      remove one group's block only
#   ./scripts/remove-schedules.sh --print [...]       print the crontab that would remain, touch nothing

set -e

PRINT_ONLY=false
GROUP=all
while [ $# -gt 0 ]; do
  case "$1" in
    --print) PRINT_ONLY=true ;;
    --group)
      [ $# -ge 2 ] || { echo "Error: --group needs a value (run, review, maintenance or all)." >&2; exit 1; }
      GROUP="$2"
      shift
      ;;
    *) echo "Usage: remove-schedules.sh [--print] [--group run|review|maintenance|all]" >&2; exit 1 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/cron-blocks.sh"

if [ "$GROUP" != "all" ]; then
  case " ${BOT_CRON_GROUPS[*]} " in
    *" $GROUP "*) ;;
    *) echo "Error: unknown group '$GROUP' (run, review, maintenance or all)." >&2; exit 1 ;;
  esac
fi

EXISTING=$(crontab -l 2>/dev/null || true)

# One group cannot be cut out of a block written before jobs were grouped.
if [ "$GROUP" != "all" ] && bot_has_ungrouped_cron_block "$BOT_PROJECT_NAME" <<< "$EXISTING"; then
  echo "Error: $BOT_PROJECT_NAME has a single crontab block from before jobs were grouped." >&2
  echo "Run 'make remove-schedules' to remove it whole, or 'make schedules' to replace it with one block per group." >&2
  exit 1
fi

if [ "$GROUP" = "all" ]; then
  STRIPPED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" <<< "$EXISTING")
else
  STRIPPED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" "$GROUP" <<< "$EXISTING")
fi
CLEANED=$(sed '/^$/N;/^\n$/d' <<< "$STRIPPED")

if $PRINT_ONLY; then
  printf '%s\n' "$CLEANED"
  exit 0
fi

if [ "$CLEANED" = "$(sed '/^$/N;/^\n$/d' <<< "$EXISTING")" ]; then
  echo "No cron jobs installed for $BOT_PROJECT_NAME${GROUP:+ ($GROUP)}; crontab left as it was."
  exit 0
fi

# An empty crontab is removed rather than installed as a blank file.
if [ -z "$(tr -d '[:space:]' <<< "$CLEANED")" ]; then
  crontab -r
else
  printf '%s\n' "$CLEANED" | crontab -
fi

echo "Cron jobs removed for $BOT_PROJECT_NAME ($GROUP)."
if [ "$GROUP" = "all" ]; then
  echo "Run 'make schedules' to install them again, or 'crontab -l' for what remains."
else
  echo "Run './scripts/sync-schedules.sh --group $GROUP' to install them again, or 'crontab -l' for what remains."
fi
