#!/bin/bash
# Gate script for review-prs cron job.
# Exits 0 if there are open PRs in the configured PR repo updated in the last day.
# Exits 1 if no recent PRs found (nothing to review), or if GitHub could not be
# asked after a few tries -- that case says why on stderr, which the cron line
# appends to the job's log.
#
# Usage in cron: ./scripts/check-new-prs.sh 2>> log && claude -p '/review-prs 1d open auto' ...

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

# Quick check: are there any open PRs updated in the last 24 hours?
CUTOFF=$(date -u -v-1d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "1 day ago" +%Y-%m-%dT%H:%M:%SZ)

# A failed query is not an empty one. Reading gh's failure as "0 PRs" is how two
# sweeps in a row were skipped with nothing in any log. A transient failure gets
# retried; one that persists is reported and the sweep skipped, the same safe
# direction check-review-requests.sh takes: no paid session on an answer we do
# not have.
#
# `gh pr list`, the query fetch-prs.py makes, and not `gh search`: the search
# API has a rate limit of its own, a few dozen calls a minute, which the
# review-request poll shares.
ATTEMPTS="${CHECK_NEW_PRS_ATTEMPTS:-3}"
RETRY_DELAY="${CHECK_NEW_PRS_RETRY_DELAY:-20}"
ERR=$(mktemp)
trap 'rm -f "$ERR"' EXIT

ASKED=0
for attempt in $(seq 1 "$ATTEMPTS"); do
  if UPDATED=$(gh pr list --repo "$BOT_PR_REPO" --state open --limit 500 \
    --json updatedAt --jq '.[].updatedAt' 2>"$ERR"); then
    ASKED=1
    break
  fi
  [ "$attempt" -lt "$ATTEMPTS" ] && sleep "$RETRY_DELAY"
done

if [ "$ASKED" -eq 0 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] check-new-prs: could not query $BOT_PR_REPO after $ATTEMPTS tries — skipping review-prs." >&2
  sed 's/^/  gh: /' "$ERR" >&2
  exit 1
fi

# ISO 8601 UTC timestamps compare correctly as strings.
if ! awk -v cutoff="$CUTOFF" '$0 >= cutoff { found = 1 } END { exit !found }' <<<"$UPDATED"; then
  echo "No open PRs updated since $CUTOFF — skipping review-prs."
  exit 1
fi

echo "Found recent open PRs — proceeding with review-prs."
exit 0
