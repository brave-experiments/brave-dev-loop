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

# Under the repo lock, not bare. A review builds a worktree per PR out of this
# same .git, reviews overlap each other, and two runs fetching into one
# repository collide on its ref locks — one of them fails with "cannot lock
# ref" and `set -e` then cancels the whole cron chain behind it. The lock is
# the one git-repo-lock.sh and lib/repo_lock.py take, so a concurrent PR-head
# fetch counts as contention too.
#
# One critical section for all three: a reset to a ref another process is
# mid-fetch on is the race, not each command on its own. git-repo-lock.sh does
# not cd, so this inherits the working directory set above.
"$SCRIPT_DIR/git-repo-lock.sh" "$TARGET_DIR" -- bash -c '
  set -e
  git checkout "$1"
  git fetch upstream
  git reset --hard "upstream/$1"
' bash "$BRANCH"

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
