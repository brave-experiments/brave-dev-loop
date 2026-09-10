#!/bin/bash
# Sync the target repo's default branch to upstream and push to origin.
# Run before review-prs so best practices are read from the latest upstream state.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

TARGET_DIR="$BOT_TARGET_REPO_DIR"
BRANCH="${BOT_DEFAULT_BRANCH:-master}"

if [ -z "$TARGET_DIR" ] || [ ! -e "$TARGET_DIR/.git" ]; then
  echo "No target repo at '$TARGET_DIR' — skipping upstream sync."
  exit 0
fi

# Projects without an upstream fork have nothing to sync. Exit 0 rather than
# failing: this script sits in an && chain in cron, so a non-zero exit here
# silently cancels run.sh, review-prs and the rest of the job.
if ! git -C "$TARGET_DIR" remote get-url upstream >/dev/null 2>&1; then
  echo "No 'upstream' remote in $TARGET_DIR — skipping upstream sync."
  exit 0
fi

echo "Syncing $TARGET_DIR ($BRANCH) to upstream/$BRANCH"

cd "$TARGET_DIR"
git checkout "$BRANCH"
git fetch upstream
git reset --hard "upstream/$BRANCH"

echo "$BRANCH synced to upstream/$BRANCH"

# In the no-fork layout origin and upstream are the same repo, so the push
# would be a no-op against the ref we just reset from. Skip it.
ORIGIN_URL=$(git remote get-url origin 2>/dev/null || echo "")
UPSTREAM_URL=$(git remote get-url upstream 2>/dev/null || echo "")
if [ -z "$ORIGIN_URL" ] || [ "${ORIGIN_URL%.git}" = "${UPSTREAM_URL%.git}" ]; then
  exit 0
fi

# Mirroring to the fork is a convenience, not a precondition for the jobs that
# run after this script. A missing fork or an SSH identity without access must
# not cancel the rest of the && chain in cron.
if ! git push origin "$BRANCH"; then
  echo "WARNING: could not push $BRANCH to origin — continuing anyway." >&2
fi
