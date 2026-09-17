#!/bin/bash
# Sync the target repo's default branch to upstream and push to origin.
# Run before review-prs so best practices are read from the latest upstream state.
#
# This script is defensive on purpose. It runs unattended from cron, ahead of
# the job that actually does the work, and a previous run killed mid-operation
# (timeout, OOM, reboot) can leave the checkout in a state that stops git dead:
# a stale index.lock, a truncated (zero-byte) loose object, a branch ref
# pointing at a commit that is no longer there. Because a cron job chains with
# `&&`, an abort here takes run.sh and review-prs down with it -- that is how
# review-prs went 8 days without running. So: heal what can be healed, never
# destroy work that is not committed, and never exit non-zero.

set -euo pipefail

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

# The whole thing runs as one critical section, under the repo lock and not
# bare. A review builds a worktree per PR out of this same .git, reviews
# overlap each other, and two runs writing to one repository collide on its ref
# locks — one of them fails with "cannot lock ref" and `set -e` then cancels
# the whole cron chain behind it. The lock is the one git-repo-lock.sh and
# lib/repo_lock.py take, so a concurrent PR-head fetch counts as contention
# too. The recovery steps below write to that same .git, so they belong inside
# the lock as well: a reset to a ref another process is mid-fetch on is the
# race, not each command on its own.
#
# Every step inside is guarded with `|| exit 1` rather than left to errexit, so
# a failure lands on the one warning below instead of half-syncing whatever
# branch we are still on.
LOCKED_SYNC=$(cat <<'EOS'
set -u
BRANCH="$1"

# --- Recover from a previous interrupted/killed run -------------------------

GIT_DIR_ABS="$(git rev-parse --absolute-git-dir)"

# Abort a half-finished merge/rebase/cherry-pick and clear a stale index lock.
git merge --abort       2>/dev/null || true
git rebase --abort      2>/dev/null || true
git cherry-pick --abort 2>/dev/null || true
rm -f "$GIT_DIR_ABS/index.lock"

# Delete zero-byte loose objects, left behind when a git write was killed
# mid-flight. Removing them lets the fetch below re-download the object instead
# of failing with "object file ... is empty".
OBJECTS_DIR="$(git rev-parse --git-path objects)"
empty_objs="$(find "$OBJECTS_DIR" -type f -size 0 2>/dev/null || true)"
if [ -n "$empty_objs" ]; then
  echo "Removing truncated git objects:"
  echo "$empty_objs"
  find "$OBJECTS_DIR" -type f -size 0 -delete 2>/dev/null || true
fi

# Prune local branch refs pointing at a missing or corrupt commit. These are
# throwaway story branches; one bad ref fails `git fetch` outright ("bad object
# refs/heads/...") and must not block the sync of the default branch.
while read -r ref; do
  [ -z "$ref" ] && continue
  if ! git rev-parse --verify --quiet "${ref}^{commit}" >/dev/null 2>&1; then
    echo "Pruning corrupt ref: $ref"
    git update-ref -d "$ref" 2>/dev/null || rm -f "$GIT_DIR_ABS/$ref"
  fi
done < <(git for-each-ref --format="%(refname)" refs/heads 2>/dev/null || true)

# --- Sync the default branch ------------------------------------------------

# Uncommitted changes are stashed, never discarded. A profile that commits in
# this checkout can have a story's work in the tree right now, and `git
# checkout` would fail on "local changes would be overwritten". Tracked files
# only -- untracked ones do not block the checkout and can be a lot of bytes.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Stashing uncommitted changes before the sync (recover with 'git stash list'):"
  git stash push -m "sync-target-repo.sh $(date -u +%Y-%m-%dT%H:%M:%SZ)" || exit 1
fi

git checkout "$BRANCH" || exit 1
git fetch upstream || exit 1
git reset --hard "upstream/$BRANCH" || exit 1
EOS
)

# git-repo-lock.sh does not cd, so this inherits the working directory set above.
if ! "$SCRIPT_DIR/git-repo-lock.sh" "$TARGET_DIR" -- bash -c "$LOCKED_SYNC" bash "$BRANCH"; then
  echo "WARNING: could not sync $BRANCH in $TARGET_DIR — leaving the checkout as it is." >&2
  exit 0
fi

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
