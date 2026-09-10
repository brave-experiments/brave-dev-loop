#!/bin/bash
# Reset run state to start a fresh iteration cycle.
# This allows all stories to be checked again.
#
# Usage:
#   ./scripts/reset-run-state.sh                          # data/run-state.json
#   ./scripts/reset-run-state.sh --slot 2                 # data/run-state.slot-2.json
#   ./scripts/reset-run-state.sh --state-file <path>      # an explicit file
#   ./scripts/reset-run-state.sh --slot 2 --config-from data/run-state.json
#
# Iteration state (runId, storiesCheckedThisRun, ...) is per run slot, so each
# slot gets its own file. The operator settings (skipPushedTasks,
# enableMergeBackoff, mergeBackoffStoryIds) are not per-slot: they are read
# from --config-from, which defaults to the file being reset, so a fresh slot
# file inherits what data/run-state.json says instead of silently defaulting.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/run-slots.sh"  # also provides the lock helpers

RUN_STATE_FILE=""
CONFIG_FROM=""
QUIET=false

while [ $# -gt 0 ]; do
  case "$1" in
    --slot) RUN_STATE_FILE=$(bot_slot_run_state_file "$PROJECT_ROOT" "$2"); shift 2 ;;
    --slot=*) RUN_STATE_FILE=$(bot_slot_run_state_file "$PROJECT_ROOT" "${1#--slot=}"); shift ;;
    --state-file) RUN_STATE_FILE="$2"; shift 2 ;;
    --state-file=*) RUN_STATE_FILE="${1#--state-file=}"; shift ;;
    --config-from) CONFIG_FROM="$2"; shift 2 ;;
    --config-from=*) CONFIG_FROM="${1#--config-from=}"; shift ;;
    --quiet) QUIET=true; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

RUN_STATE_FILE="${RUN_STATE_FILE:-$PROJECT_ROOT/data/run-state.json}"
CONFIG_FROM="${CONFIG_FROM:-$RUN_STATE_FILE}"

say() { [ "$QUIET" = true ] || echo "$@"; }

say "Resetting run state ($RUN_STATE_FILE)..."

mkdir -p "$(dirname "$RUN_STATE_FILE")"

# Slot 1's state file is also the file every other slot reads its operator
# settings from, so this runs under a lock and writes by rename. Truncating in
# place let a slot starting at the same moment read an empty file — and `jq`
# on an empty file exits 0 printing nothing, so the fallbacks below did not
# fire and the reset wrote `"enableMergeBackoff": ,` — invalid JSON that broke
# the run that read it next.
STATE_LOCK="$(dirname "$RUN_STATE_FILE")/.run-state.lock"
if ! bot_acquire_lock_wait "$STATE_LOCK" 60; then
  echo "Warning: could not take the run-state lock after 60s — continuing." >&2
fi
trap bot_release_lock EXIT INT TERM HUP

# Preserve configuration settings (not iteration state). An empty result means
# the source file was unreadable or mid-write; use the documented default
# rather than writing a broken file.
# `//` yields its right side when the left is null *or false*, so reading a
# boolean with it turns an explicit `false` back into the default — which is
# how `enableMergeBackoff: false` used to un-set itself on the next run.
SKIP_PUSHED=$(jq -r 'if .skipPushedTasks == null then false else .skipPushedTasks end' "$CONFIG_FROM" 2>/dev/null || echo "")
ENABLE_MERGE_BACKOFF=$(jq -r 'if .enableMergeBackoff == null then true else .enableMergeBackoff end' "$CONFIG_FROM" 2>/dev/null || echo "")
MERGE_BACKOFF_STORY_IDS=$(jq -c '.mergeBackoffStoryIds // null' "$CONFIG_FROM" 2>/dev/null || echo "")
SKIP_PUSHED="${SKIP_PUSHED:-false}"
ENABLE_MERGE_BACKOFF="${ENABLE_MERGE_BACKOFF:-true}"
MERGE_BACKOFF_STORY_IDS="${MERGE_BACKOFF_STORY_IDS:-null}"

# Build with jq so a bad value fails here instead of producing a file that
# only breaks later, and install by rename so no reader sees a partial file.
TMP_STATE=$(mktemp "$(dirname "$RUN_STATE_FILE")/.run-state.XXXXXX")
if ! jq -n \
  --argjson skipPushed "$SKIP_PUSHED" \
  --argjson enableMergeBackoff "$ENABLE_MERGE_BACKOFF" \
  --argjson mergeBackoffStoryIds "$MERGE_BACKOFF_STORY_IDS" \
  '{
    runId: null,
    storiesCheckedThisRun: [],
    skipPushedTasks: $skipPushed,
    enableMergeBackoff: $enableMergeBackoff,
    mergeBackoffStoryIds: $mergeBackoffStoryIds,
    notes: [
      "This file tracks iteration state within a single run",
      "runId: Timestamp when this run started (null = needs initialization)",
      "storiesCheckedThisRun: Story IDs that have been processed in this run",
      "skipPushedTasks: Set to true to skip all '"'"'pushed'"'"' status tasks and only work on new development"
    ],
    lastIterationHadStateChange: false,
    currentIterationLogPath: null
  }' > "$TMP_STATE"; then
  rm -f "$TMP_STATE"
  echo "Error: could not build run state from $CONFIG_FROM" >&2
  exit 1
fi
mv "$TMP_STATE" "$RUN_STATE_FILE"

say "✓ Run state reset successfully"
say "  - runId: null (will be initialized on next iteration)"
say "  - storiesCheckedThisRun: [] (empty)"
say "  - skipPushedTasks: $SKIP_PUSHED (from $(basename "$CONFIG_FROM"))"
say "  - enableMergeBackoff: $ENABLE_MERGE_BACKOFF (from $(basename "$CONFIG_FROM"))"
say "  - mergeBackoffStoryIds: $MERGE_BACKOFF_STORY_IDS (from $(basename "$CONFIG_FROM"))"
say ""
say "Next iteration will start a fresh run and can check all stories again."
