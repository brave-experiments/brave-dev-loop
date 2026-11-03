#!/bin/bash
# The review-request queue: open PRs where a human clicked "Request review" (or
# the re-request arrow) and named the account that reviews (the reviewer, else the bot).
#
# Sourced by scripts/check-review-requests.sh (the cron gate) and
# scripts/review-requested.sh (the job it gates). One definition, because a gate
# that asks a different question than its job either wakes the job for nothing
# or starves it.
#
# Requires: lib/load-config.sh already sourced (BOT_PR_REPO, BOT_REVIEW_USERNAME).

# Print one PR number per line, newest first, at most `limit` of them. Returns
# the query's exit code, so a caller can tell an empty queue (0, no output) from a
# failed query.
#
# The limit applies to the members' PRs. GitHub is asked for a wider window
# (BOT_REVIEW_QUEUE_WINDOW) and the org filter runs before the limit, because
# PRs from non-members fill the newest slots and, with the limit on the query
# itself, pushed a member's older PR out of the queue for good.
#
#   bot_review_requested_prs [limit]
bot_review_requested_prs() {
  # GitHub drops the bot from reviewRequests the moment it submits a review, so
  # this query is the whole state machine: a PR leaves the queue by being
  # reviewed and re-enters it when someone asks again. Nothing local is
  # consulted, which is what makes the answer the same on every machine.
  #
  # PRs whose author is not a registered org member never leave this function:
  # org_gate.py drops them, with no model involved and nothing posted on the
  # PR, so neither the gate nor the job ever sees them. A members file it
  # cannot read fails the query, which callers treat as "do nothing".
  gh pr list \
    --repo "$BOT_PR_REPO" \
    --search "review-requested:$BOT_REVIEW_USERNAME" \
    --state open \
    --limit "${BOT_REVIEW_QUEUE_WINDOW:-200}" \
    --json number,author \
    --jq '.[] | "\(.number) \(.author.login)"' |
    python3 "$BOT_DIR/scripts/lib/org_gate.py" filter-queue |
    awk -v limit="${1:-50}" 'NR <= limit'
}

# The ping queue: threads (issues or PRs, in the PR repo and the issue repo) where
# an org member @-mentioned the reviewer account and nothing of ours has been
# posted since. One line per ping, "<repo> <number> <kind> <comment id> <pinger>"
# separated by tabs, longest-waiting first. Same contract as the review queue: the
# exit code tells an empty queue (0, no output) from a failed query, and the
# members' filter lives in lib/pings.py, so neither the gate nor the job ever sees
# a ping from someone outside the org. See lib/pings.py for the rest.

# The arguments every lib/pings.py command takes, in BOT_PING_ARGS. Both of our
# accounts count as "us": a comment from either answers a ping.
bot_ping_args() {
  BOT_PING_ARGS=(--login "$BOT_REVIEW_USERNAME")
  local own
  for own in "$BOT_USERNAME" "${BOT_REVIEWER_USERNAME:-}"; do
    [ -n "$own" ] && BOT_PING_ARGS+=(--self "$own")
  done
  return 0
}

# The queue searches the PR repo and the issue repo: a ping can be on either, and
# for some projects they are not the same repository.
#
#   bot_ping_queue [limit]
bot_ping_queue() {
  bot_ping_args
  python3 "$BOT_DIR/scripts/lib/pings.py" queue "${BOT_PING_ARGS[@]}" \
    --repo "$BOT_PR_REPO" --repo "$BOT_ISSUE_REPO" --limit "${1:-20}"
}
