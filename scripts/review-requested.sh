#!/bin/bash
# Answer the review requests a human made of the bot: one /review-prs session per
# PR in the queue.
#
#   ./scripts/review-requested.sh              # the queue, up to the cap
#   REVIEW_REQUESTED_MAX_PRS=1 ./scripts/review-requested.sh
#
# Runs are allowed to overlap. The poll fires every five minutes and a review
# takes far longer than that, so waiting for the previous run would leave a
# backlog draining at one PR per session however often the poll ran. Two things
# make overlap safe:
#
#   * A lock per PR, held for the life of its session. GitHub only drops a PR
#     from the queue once the review is submitted, so an overlapping run sees
#     the PR that is being reviewed right now at the front of its own queue and
#     has to be told to walk past it — otherwise every run starts on the same
#     PR and posts the same review.
#
#   * A bounded number of runs, enforced by the caller
#     (`with-lock.sh review-prs --slots N`). Each session fans out to a subagent
#     per rule document, so unbounded overlap is unbounded concurrency against
#     the API, and the poll would add to it every five minutes.
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
source "$SCRIPT_DIR/lib/lock.sh"
source "$SCRIPT_DIR/lib/review-requests.sh"

MAX_PRS="${REVIEW_REQUESTED_MAX_PRS:-5}"

LOCK_DIR="$BOT_DIR/.ignore"
mkdir -p "$LOCK_DIR"

# Exit code the per-PR subshell uses for "someone else has this one". Distinct
# from anything the agent itself returns, so a PR another run is reviewing is
# never counted as a failed review.
PR_BUSY_RC=75

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
BUSY=""
DEFERRED=""

for PR in $PRS; do
  if [ "$REVIEWED" -ge "$MAX_PRS" ]; then
    DEFERRED="$DEFERRED #$PR"
    continue
  fi

  # A subshell holds the PR lock for the life of the session, so it is released
  # the moment the session ends however it ends — including a kill, because the
  # lock is the kernel's and not a file left behind on disk.
  #
  # `200>&-` for the same reason with-lock.sh does it: a subagent that outlived
  # the session having inherited the lock fd would hold this PR's lock with
  # nothing running, and no later poll would ever review that PR again.
  RC=0
  (
    bot_acquire_lock "$LOCK_DIR/.review-pr-$PR.lock" || exit "$PR_BUSY_RC"
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] reviewing #$PR — review requested from $BOT_USERNAME"
    "$BOT_CLAUDE_BIN" -p "/review-prs #$PR open auto" --allowedTools "$BOT_AGENT_TOOLS" 200>&-
  ) || RC=$?

  if [ "$RC" -eq "$PR_BUSY_RC" ]; then
    # Another run is reviewing it. Not an error, and not work for this run:
    # move down the queue rather than waiting or reviewing it twice.
    BUSY="$BUSY #$PR"
    continue
  fi

  REVIEWED=$((REVIEWED + 1))
  if [ "$RC" -ne 0 ]; then
    echo "Review of #$PR failed." >&2
    FAILED=$((FAILED + 1))
  fi
done

if [ -n "$BUSY" ]; then
  echo "Already being reviewed by another run; skipped$BUSY"
fi

if [ -n "$DEFERRED" ]; then
  echo "Capped at $MAX_PRS per run; leaving$DEFERRED for the next poll."
fi

[ "$FAILED" -eq 0 ]
