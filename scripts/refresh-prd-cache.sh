#!/bin/bash
# Refresh the PRD cache from GitHub. Only does anything when
# project.prdMode is "auto", where the PRD is a cache rather than an
# authored document.
#
# Both syncs are plain Python against the GitHub API — no agent, no tokens.
# Existing stories are never modified, so status set by update-prd-status.py
# survives a refresh.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

if [ "$BOT_PRD_MODE" != "auto" ]; then
  exit 0
fi

PRD_FILE="$BOT_DIR/data/prd.json"
if [ ! -f "$PRD_FILE" ]; then
  mkdir -p "$(dirname "$PRD_FILE")"
  printf '{\n  "stories": []\n}\n' > "$PRD_FILE"
  echo "Created empty PRD cache at $PRD_FILE"
fi

# Neither sync is fatal: a GitHub hiccup should not cancel the run, which can
# still work the stories already cached.
python3 "$SCRIPT_DIR/add-backlog-to-prd.py" || echo "WARNING: issue sync failed — continuing with the cached PRD." >&2
python3 "$SCRIPT_DIR/sync-bot-prs-to-prd.py" || echo "WARNING: PR sync failed — continuing with the cached PRD." >&2
