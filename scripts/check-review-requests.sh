#!/bin/bash
# Gate script for the review-requested cron job.
# Exits 0 if an open PR in the configured PR repo is waiting on a review the bot
# was explicitly asked for. Exits 1 when the queue is empty — one GitHub API
# call, no agent session, no tokens.
#
# Usage in cron:
#   ./scripts/check-review-requests.sh && ./scripts/with-lock.sh review-prs -- ./scripts/review-requested.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/review-requests.sh"

if ! PRS=$(bot_review_requested_prs); then
  # A failed query is not an empty queue, but the safe direction is the same:
  # never start a paid session on an answer we do not have.
  echo "Could not query review requests for $BOT_USERNAME — skipping." >&2
  exit 1
fi

if [ -z "$PRS" ]; then
  echo "No review requests for $BOT_USERNAME in $BOT_PR_REPO — nothing to do."
  exit 1
fi

echo "Review requested from $BOT_USERNAME on: $(echo "$PRS" | tr '\n' ' ')"
exit 0
