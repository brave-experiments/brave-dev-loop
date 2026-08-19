#!/bin/bash
# Gate script for the discover-bugs cron job.
# Exits 0 if the target repo has scannable source changes since the last scan.
# Exits 1 otherwise (no target repo, or nothing new to scan).
#
# Usage in cron: ./scripts/check-scan-candidates.sh && ... && $CLAUDE_BIN -p '/discover-bugs --since-cache auto'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

TARGET="$BOT_TARGET_REPO_PATH"
if [[ "$TARGET" != /* ]]; then
  TARGET="$(cd "$BOT_DIR/.." && pwd)/$TARGET"
fi

if [ ! -d "$TARGET/.git" ]; then
  echo "No target repo at $TARGET — skipping scan."
  exit 1
fi

CACHE="$BOT_DIR/.ignore/discover-bugs-cache.json"
LAST_SHA=""
[ -f "$CACHE" ] && LAST_SHA=$(jq -r '.lastScanSha // empty' "$CACHE" 2>/dev/null)

# Determine changed source files since last scan (or last 7 days on first run).
if [ -n "$LAST_SHA" ] && git -C "$TARGET" rev-parse --verify --quiet "${LAST_SHA}^{commit}" >/dev/null; then
  CHANGED=$(git -C "$TARGET" diff --name-only "${LAST_SHA}..HEAD")
else
  CHANGED=$(git -C "$TARGET" log --since="7 days ago" --name-only --pretty=format: HEAD)
fi

# Count scannable source files (exclude tests/third_party/generated).
COUNT=$(echo "$CHANGED" \
  | grep -Ei '\.(cc|cpp|cxx|mm|m|h|hpp|ts|tsx|js)$' \
  | grep -Ev '/(test|tests|third_party|node_modules|gen)/' \
  | grep -Ev '(_unittest|_browsertest)\.' \
  | sort -u | grep -c . || true)

if [ "${COUNT:-0}" -eq 0 ]; then
  echo "No scannable source changes since last scan — skipping."
  exit 1
fi

echo "$COUNT scannable changed file(s) — proceeding with scan."
exit 0
