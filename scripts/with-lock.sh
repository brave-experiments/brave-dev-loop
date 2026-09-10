#!/bin/bash
# Wrapper that ensures only one instance of a job runs at a time.
# Also kills the job if it exceeds a timeout (default: 2 hours).
#
# Usage: with-lock.sh <lock-name> [--timeout <seconds>] -- <command...>
# Example: with-lock.sh review-prs -- claude -p '/review-prs ...'
#          with-lock.sh review-prs --timeout 3600 -- claude -p '/review-prs ...'

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_DIR="$SCRIPT_DIR/../.ignore"
mkdir -p "$LOCK_DIR"

LOCK_NAME="$1"
shift

if [ -z "$LOCK_NAME" ]; then
  echo "Usage: with-lock.sh <lock-name> [--timeout <seconds>] -- <command...>"
  exit 1
fi

# Default timeout: 2 hours
TIMEOUT=7200

# Parse optional --timeout
while [ "$1" != "--" ] && [ $# -gt 0 ]; do
  case "$1" in
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Skip the "--" separator
if [ "$1" = "--" ]; then
  shift
fi

if [ $# -eq 0 ]; then
  echo "No command specified after --"
  exit 1
fi

LOCKFILE="$LOCK_DIR/.${LOCK_NAME}.lock"

# Acquire exclusive lock or exit
source "$SCRIPT_DIR/lib/lock.sh"
bot_acquire_lock "$LOCKFILE"
case $? in
  0) ;;
  1) echo "Another $LOCK_NAME job is already running. Exiting."; exit 0 ;;
  *) echo "Could not acquire the $LOCK_NAME lock. Exiting." >&2; exit 1 ;;
esac
trap bot_release_lock EXIT INT TERM HUP

# Log the model being used (extract --model value from command args)
_model=""
for _arg in "$@"; do
  if [ "$_prev" = "--model" ]; then _model="$_arg"; break; fi
  _prev="$_arg"
done
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $LOCK_NAME starting (model=${_model:-unset})"

# Run the command with a timeout (use timeout-tree to kill entire process tree)
SCRIPT_DIR_LOCK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR_LOCK/timeout-tree.sh" "$TIMEOUT" "$@" 200>&-
EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
  echo "$LOCK_NAME job killed after exceeding timeout of ${TIMEOUT}s"
fi

exit $EXIT_CODE
