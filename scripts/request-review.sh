#!/bin/bash
# Ask the reviewer account for another look at a pull request the bot just pushed to.
#
# Usage: request-review.sh [--repo owner/name] <pr-number>
#
# GitHub drops a reviewer from the request list once they submit a review, so a
# follow-up push (review fixes, a rebase) leaves the PR out of their queue until
# someone asks again. The review-request poll only sees PRs that were asked for,
# which makes this call the push's second half.
#
# Run as the bot, which authors the PR. With no reviewer configured
# (reviewer.username in config.json) there is no one to ask and this does nothing.
# A PR that is no longer open is skipped: the API refuses the request anyway.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

repo="$BOT_PR_REPO"
if [ "${1:-}" = "--repo" ]; then
  repo="${2:?Usage: request-review.sh [--repo owner/name] <pr-number>}"
  shift 2
fi
pr="${1:-}"
if ! [[ "$pr" =~ ^[0-9]+$ ]] || [ $# -ne 1 ]; then
  echo "Usage: request-review.sh [--repo owner/name] <pr-number>" >&2
  exit 2
fi

if [ -z "$BOT_REVIEWER_USERNAME" ]; then
  echo "No reviewer account configured; not requesting a review on $repo#$pr."
  exit 0
fi

state=$(gh pr view "$pr" --repo "$repo" --json state --jq .state)
if [ "$state" != "OPEN" ]; then
  echo "$repo#$pr is $state; not requesting a review."
  exit 0
fi

gh api --method POST "repos/$repo/pulls/$pr/requested_reviewers" \
  -f "reviewers[]=$BOT_REVIEWER_USERNAME" > /dev/null
echo "Requested a review from $BOT_REVIEWER_USERNAME on $repo#$pr."
