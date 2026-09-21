#!/bin/bash
# The review-request queue: open PRs where a human clicked "Request review" (or
# the re-request arrow) and named the bot.
#
# Sourced by scripts/check-review-requests.sh (the cron gate) and
# scripts/review-requested.sh (the job it gates). One definition, because a gate
# that asks a different question than its job either wakes the job for nothing
# or starves it.
#
# Requires: lib/load-config.sh already sourced (BOT_PR_REPO, BOT_USERNAME).

# Print one PR number per line, newest first. Returns gh's exit code, so a
# caller can tell an empty queue (0, no output) from a failed query.
#
#   bot_review_requested_prs [limit]
bot_review_requested_prs() {
  # GitHub drops the bot from reviewRequests the moment it submits a review, so
  # this query is the whole state machine: a PR leaves the queue by being
  # reviewed and re-enters it when someone asks again. Nothing local is
  # consulted, which is what makes the answer the same on every machine.
  gh pr list \
    --repo "$BOT_PR_REPO" \
    --search "review-requested:$BOT_USERNAME" \
    --state open \
    --limit "${1:-20}" \
    --json number \
    --jq '.[].number'
}
