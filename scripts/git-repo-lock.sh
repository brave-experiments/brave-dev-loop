#!/bin/bash
# Serialize git operations that touch a shared .git directory.
#
# Worktrees share one .git. Two runs doing `git fetch` or `git worktree add`
# in it at the same moment collide on git's own ref locks and one of them
# fails with "cannot lock ref". Work *inside* a worktree needs no lock — only
# operations against the shared repository do.
#
# Usage:
#   ./scripts/git-repo-lock.sh <repo-dir> -- git fetch origin
#   ./scripts/git-repo-lock.sh <repo-dir> -- git worktree add -b fix-x ../repo-133 origin/main
#
# Waits (up to --timeout seconds, default 300) rather than giving up: these
# are short operations and skipping one would break the caller.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/lock.sh"

REPO_DIR="$1"; shift || true
TIMEOUT=300

while [ "${1:-}" != "--" ] && [ $# -gt 0 ]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --timeout=*) TIMEOUT="${1#--timeout=}"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
[ "${1:-}" = "--" ] && shift

if [ -z "$REPO_DIR" ] || [ $# -eq 0 ]; then
  sed -n '2,16p' "$0"
  exit 1
fi
if [ ! -e "$REPO_DIR/.git" ]; then
  echo "Error: '$REPO_DIR' is not a git repository" >&2
  exit 1
fi

# One lock per repository, named after its absolute path so worktrees of the
# same repo — which share its .git — share the lock too.
REPO_ABS="$(cd "$REPO_DIR" && pwd)"
REPO_KEY=$(printf '%s' "$REPO_ABS" | shasum | cut -c1-12)
mkdir -p "$BOT_DIR/.ignore"
LOCKFILE="$BOT_DIR/.ignore/.git-$REPO_KEY.lock"

if ! bot_acquire_lock_wait "$LOCKFILE" "$TIMEOUT"; then
  echo "Timed out after ${TIMEOUT}s waiting for the git lock on $REPO_ABS." >&2
  echo "Check ./scripts/run-status.sh — another run may be wedged." >&2
  exit 1
fi
trap bot_release_lock EXIT INT TERM HUP

"$@"
