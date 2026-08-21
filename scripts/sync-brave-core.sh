#!/bin/bash
# Sync brave-core master to upstream/master and push to origin.
# Run before review-prs so best practices are read from the latest upstream state.
#
# This script is defensive on purpose: it runs unattended from cron, and a
# previous run that was killed mid-operation (timeout, OOM, reboot) can leave the
# checkout in a broken state — a dirty working tree, a stale index.lock, a
# truncated (zero-byte) loose object, or a branch ref pointing at a corrupt
# commit. Any of those would otherwise abort the sync, and because the cron line
# chains with `&&`, the whole job (including review-prs) silently stops running.
# So we self-heal the common breakages before doing the actual sync.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

BRAVE_CORE_DIR="$(cd "$SCRIPT_DIR/../$BOT_TARGET_REPO_PATH" && pwd)"

echo "Syncing brave-core at $BRAVE_CORE_DIR"
cd "$BRAVE_CORE_DIR"

# --- Recover from a previous interrupted/killed run -------------------------

# Abort any half-finished merge/rebase/cherry-pick and clear a stale index lock.
git merge --abort       2>/dev/null || true
git rebase --abort      2>/dev/null || true
git cherry-pick --abort 2>/dev/null || true
rm -f .git/index.lock

# Remove zero-byte (truncated) loose objects left behind when a git write was
# killed mid-flight. Deleting them lets the fetch below re-download them instead
# of failing with "object file ... is empty".
empty_objs="$(find .git/objects -type f -size 0 2>/dev/null || true)"
if [ -n "$empty_objs" ]; then
  echo "Removing truncated git objects:"
  echo "$empty_objs"
  find .git/objects -type f -size 0 -delete 2>/dev/null || true
fi

# Prune local branch refs that point at a missing/corrupt commit. These are
# throwaway bot work branches; a single bad one breaks `git fetch` ("bad object
# refs/heads/...") and must not block the master sync.
while read -r ref; do
  [ -z "$ref" ] && continue
  if ! git rev-parse --verify --quiet "${ref}^{commit}" >/dev/null 2>&1; then
    echo "Pruning corrupt ref: $ref"
    git update-ref -d "$ref" 2>/dev/null || rm -f ".git/$ref"
  fi
done < <(git for-each-ref --format='%(refname)' refs/heads 2>/dev/null || true)

# --- Sync master ------------------------------------------------------------

# Force-checkout discards any local edits to tracked files left in the working
# tree (a previous run may have applied a patch), so the switch can't be blocked
# by "local changes would be overwritten by checkout".
git checkout -f master
git fetch upstream
git reset --hard upstream/master
git push origin master

echo "brave-core master synced to upstream/master"
