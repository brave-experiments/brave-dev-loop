#!/bin/bash
# Append a block to data/progress.txt under a lock.
#
# Several runs write the same progress log. A plain `cat >> progress.txt` from
# two agents at once interleaves their lines mid-entry, and the result is
# unreadable exactly when you most need it. This takes the lock, appends, and
# releases — the whole block lands as one piece.
#
# Usage:
#   ./scripts/append-progress.sh <<'EOF'
#   ## US-004 — pushed
#   ...
#   EOF
#
#   echo "one line" | ./scripts/append-progress.sh
#   ./scripts/append-progress.sh --file note.txt

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/lock.sh"

PROGRESS_FILE="$BOT_DIR/data/progress.txt"
INPUT_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --file) INPUT_FILE="$2"; shift 2 ;;
    --file=*) INPUT_FILE="${1#--file=}"; shift ;;
    --progress-file) PROGRESS_FILE="$2"; shift 2 ;;
    --progress-file=*) PROGRESS_FILE="${1#--progress-file=}"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "$(dirname "$PROGRESS_FILE")"

LOCKFILE="$BOT_DIR/data/.progress.lock"
if ! bot_acquire_lock_wait "$LOCKFILE" 60; then
  echo "Could not take the progress lock after 60s — appending anyway." >&2
fi
trap bot_release_lock EXIT INT TERM HUP

if [ -n "$INPUT_FILE" ]; then
  cat "$INPUT_FILE" >> "$PROGRESS_FILE"
else
  cat >> "$PROGRESS_FILE"
fi
# A trailing newline keeps the next append from starting mid-line.
tail -c 1 "$PROGRESS_FILE" | od -c | grep -q '\\n' || echo "" >> "$PROGRESS_FILE"
