#!/bin/bash
# Gate script for a per-pod discover-bugs cron job.
# Exits 0 if the pod has work to scan (baseline not finished, or changed files
# since the last scan). Exits 1 otherwise (no target repo, or nothing to do).
#
# Usage in cron:
#   ./scripts/check-pod-candidates.sh <pod-id> && ... && $CLAUDE_BIN -p '/discover-bugs --pod <pod-id> auto file-issues'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

POD="$1"
if [ -z "$POD" ]; then
  echo "usage: check-pod-candidates.sh <pod-id>" >&2
  exit 1
fi

TARGET="$BOT_TARGET_REPO_PATH"
if [[ "$TARGET" != /* ]]; then
  TARGET="$(cd "$BOT_DIR/.." && pwd)/$TARGET"
fi

if [ ! -d "$TARGET/.git" ]; then
  echo "No target repo at $TARGET — skipping $POD scan."
  exit 1
fi

# prepare-scan.py --check prints yes/no and exits 0 (work) / 1 (nothing).
python3 "$BOT_DIR/.claude/skills/discover-bugs/prepare-scan.py" --pod "$POD" --check
exit $?
