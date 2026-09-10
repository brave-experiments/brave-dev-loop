#!/bin/bash
# Bring data/prd.json up to date with GitHub. Both syncs are plain Python
# against the API — no agent is started, so this costs no tokens.
#
# The backlog sync appends stories for newly assigned issues and never touches
# one the PRD already has, so it runs in either mode. A curated PRD is
# authored, not a list of every issue somebody remembered to type in by hand;
# before this was a script, that same append ran nightly as an agent session.
#
# The bot-PR sync rewrites story status from the bot's open PRs, which is only
# true of a PRD that is a cache. It runs in "auto" alone: a curated PRD is
# never rewritten from GitHub behind the operator.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

PRD_FILE="$BOT_DIR/data/prd.json"
if [ ! -f "$PRD_FILE" ]; then
  mkdir -p "$(dirname "$PRD_FILE")"
  printf '{\n  "stories": []\n}\n' > "$PRD_FILE"
  echo "Created empty PRD at $PRD_FILE"
fi

# Neither sync is fatal: a GitHub hiccup should not cancel the run, which can
# still work the stories already in the PRD.
python3 "$SCRIPT_DIR/add-backlog-to-prd.py" || echo "WARNING: issue sync failed — continuing with the PRD as it stands." >&2

if [ "$BOT_PRD_MODE" = "auto" ]; then
  python3 "$SCRIPT_DIR/sync-bot-prs-to-prd.py" || echo "WARNING: PR sync failed — continuing with the cached PRD." >&2
fi
