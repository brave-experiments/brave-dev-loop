#!/bin/bash
# Kill any running run.sh (and its entire process tree), release the lock,
# reset run state, and sync the target repo to upstream/master.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCKFILE="$SCRIPT_DIR/.run.lock"
source "$SCRIPT_DIR/scripts/lib/load-config.sh"

GIT_REPO="$BOT_TARGET_REPO_DIR"

# Recursively kill a process and all its descendants (leaf-first)
kill_tree() {
  local sig="${1:-TERM}"
  local pid="$2"
  local children
  children=$(pgrep -P "$pid" 2>/dev/null) || true
  for child in $children; do
    kill_tree "$sig" "$child"
  done
  kill -"$sig" "$pid" 2>/dev/null || true
}

echo "=== Emergency Reset ==="

# --- 1. Kill process(es) holding the lock ---
if [ -f "$LOCKFILE" ]; then
  LOCK_PIDS=$(fuser "$LOCKFILE" 2>/dev/null || true)
  if [ -n "$LOCK_PIDS" ]; then
    echo "Killing process tree for PIDs holding lock: $LOCK_PIDS"
    for pid in $LOCK_PIDS; do
      kill_tree TERM "$pid"
    done
    sleep 2
    # Force-kill any survivors
    SURVIVORS=$(fuser "$LOCKFILE" 2>/dev/null || true)
    if [ -n "$SURVIVORS" ]; then
      echo "Force-killing survivors: $SURVIVORS"
      for pid in $SURVIVORS; do
        kill_tree KILL "$pid"
      done
      sleep 1
    fi
  else
    echo "No process holding the lock (lock file exists but is free)."
  fi
  rm -f "$LOCKFILE"
  echo "Lock file removed."
else
  echo "No lock file found — nothing to kill."
fi

# --- 2. Reset run state ---
echo ""
echo "Resetting run state..."
"$SCRIPT_DIR/scripts/reset-run-state.sh"

# --- 3. Sync target repo to upstream/master ---
echo ""
echo "Syncing $GIT_REPO to upstream/master..."
if [ ! -d "$GIT_REPO/.git" ]; then
  echo "Warning: $GIT_REPO is not a git repo — skipping sync."
  exit 0
fi

cd "$GIT_REPO"
git stash --include-untracked 2>/dev/null || true
git checkout "$BOT_DEFAULT_BRANCH"

# No-fork deployments have no separate upstream remote — sync from origin.
SYNC_REMOTE=upstream
if ! git remote get-url upstream >/dev/null 2>&1; then
  SYNC_REMOTE=origin
fi
git fetch "$SYNC_REMOTE"
git reset --hard "$SYNC_REMOTE/$BOT_DEFAULT_BRANCH"

echo ""
echo "=== Reset complete. Ready to run ./run.sh ==="
