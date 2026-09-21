#!/bin/bash
# Answer the review requests a human made of the bot: one /review-prs session per
# PR in the queue.
#
#   ./scripts/review-requested.sh              # the queue, up to the cap
#   REVIEW_REQUESTED_MAX_PRS=1 ./scripts/review-requested.sh
#
# Run it under the same lock as the unsolicited best-practices sweep
# (`with-lock.sh review-prs`), so the two never build PR worktrees at once —
# each one is a full checkout of the target repo.
#
# One session per PR rather than one session for the queue: a review is orders
# of magnitude more context than the orchestration around it, so a session that
# took the whole queue would be deciding what to cut by the third PR. The skill
# fans out to subagents per chunk within a PR; this is the same split one level
# up, and it also keeps one PR that wedges from taking the rest of the queue
# down with it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/review-requests.sh"

MAX_PRS="${REVIEW_REQUESTED_MAX_PRS:-2}"

if ! PRS=$(bot_review_requested_prs); then
  echo "Could not query review requests for $BOT_USERNAME." >&2
  exit 1
fi

if [ -z "$PRS" ]; then
  # The gate already asked, but the queue is GitHub's and it moves: another
  # machine may have answered every one of these in between.
  echo "No review requests for $BOT_USERNAME in $BOT_PR_REPO — nothing to do."
  exit 0
fi

REVIEWED=0
FAILED=0
DEFERRED=""

for PR in $PRS; do
  if [ "$REVIEWED" -ge "$MAX_PRS" ]; then
    DEFERRED="$DEFERRED #$PR"
    continue
  fi
  REVIEWED=$((REVIEWED + 1))
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] reviewing #$PR — review requested from $BOT_USERNAME"
  if ! "$BOT_CLAUDE_BIN" -p "/review-prs #$PR open auto" --allowedTools "$BOT_AGENT_TOOLS"; then
    echo "Review of #$PR failed." >&2
    FAILED=$((FAILED + 1))
  fi
done

if [ -n "$DEFERRED" ]; then
  echo "Capped at $MAX_PRS per run; leaving$DEFERRED for the next poll."
fi

[ "$FAILED" -eq 0 ]
