#!/bin/bash
# Gate script for run.sh cron job.
# Exits 0 if prd.json has actionable stories (not all merged/skipped/invalid).
# Exits 1 if no prd.json or no actionable work remains.
#
# Usage in cron: ./scripts/check-has-work.sh && git fetch origin && ... && ./run.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PRD_FILE="$BOT_DIR/data/prd.json"
source "$SCRIPT_DIR/lib/load-config.sh"

# In auto mode the PRD is a cache, so a missing or empty one just means it has
# not been built yet — the refresh in run.sh will populate it.
if [ "$BOT_PRD_MODE" = "auto" ]; then
  echo "PRD is in auto mode — run.sh will refresh it."
  exit 0
fi

if [ ! -f "$PRD_FILE" ]; then
  echo "No prd.json found — skipping run."
  exit 1
fi

# Count stories with actionable statuses (anything not merged/skipped/invalid)
ACTIONABLE=$(jq '[.stories[] | select(.status | IN("merged","skipped","invalid") | not)] | length' "$PRD_FILE" 2>/dev/null || echo "0")

if [ "$ACTIONABLE" -eq 0 ]; then
  echo "No actionable stories in prd.json — skipping run."
  exit 1
fi

echo "$ACTIONABLE actionable stories found — proceeding."
exit 0
