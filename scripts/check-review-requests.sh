#!/bin/bash
# Gate script for the review-requested cron job.
# Exits 0 if an open PR in the configured PR repo is waiting on a review the bot
# was explicitly asked for, or an org member has @-mentioned the reviewer account
# in a thread and nobody has answered. Exits 1 when both queues are empty — a
# couple of GitHub API calls, no agent session, no tokens.
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
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Could not query review requests for $BOT_REVIEW_USERNAME — skipping." >&2
  exit 1
fi

if [ -n "$PRS" ]; then
  echo "Review requested from $BOT_REVIEW_USERNAME on: $(echo "$PRS" | tr '\n' ' ')"
  exit 0
fi

# No review is waiting. A member's @-mention of the account is the other way to
# reach it, and it is polled on the same schedule: one more search per repo, and
# only when there is no review to start the job for anyway.
if ! PINGS=$(bot_ping_queue); then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Could not query pings for $BOT_REVIEW_USERNAME — skipping." >&2
  exit 1
fi

if [ -z "$PINGS" ]; then
  echo "No review requests or pings for $BOT_REVIEW_USERNAME in $BOT_PR_REPO — nothing to do."
  exit 1
fi

echo "Pinged $BOT_REVIEW_USERNAME on: $(echo "$PINGS" | awk -F'\t' '{printf "%s#%s ", $1, $2}')"
exit 0
