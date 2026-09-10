#!/bin/bash
# Emergency reset: stop run(s), free their slots, reset their run state, and
# sync the target repo to upstream.
#
# Usage:
#   ./scripts/reset-run.sh              # every slot (default)
#   ./scripts/reset-run.sh --slot 2     # just slot 2
#   ./scripts/reset-run.sh --stale      # only orphaned slots; leaves live runs alone
#   ./scripts/reset-run.sh --no-sync    # skip the target-repo sync
#
# A slot is free when its lock can be taken, not when its files are gone. This
# script kills the processes still holding the lock — the run itself, and any
# child that inherited the lock fd and outlived it — then confirms the lock
# actually came free.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$SCRIPT_DIR/scripts/lib/load-config.sh"
source "$SCRIPT_DIR/scripts/lib/run-slots.sh"

GIT_REPO="$BOT_TARGET_REPO_DIR"
TARGET_SLOT=""
STALE_ONLY=false
DO_SYNC=true

while [ $# -gt 0 ]; do
  case "$1" in
    --slot) TARGET_SLOT="$2"; shift 2 ;;
    --slot=*) TARGET_SLOT="${1#--slot=}"; shift ;;
    --stale) STALE_ONLY=true; shift ;;
    --no-sync) DO_SYNC=false; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [ -n "$TARGET_SLOT" ]; then
  case "$TARGET_SLOT" in
    ''|*[!0-9]*) echo "Error: --slot takes a number" >&2; exit 1 ;;
  esac
fi

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

# Every pid that could be keeping this slot's lock alive: the recorded owner,
# whatever the metadata says, and anything with the lock file open (which
# catches a child that inherited the fd — the case the old `fuser` call on an
# unlocked file always missed).
slot_pids() {
  local lockfile="$1" meta="$2" pid
  {
    bot_lock_owner_pid "$lockfile"
    bot_lock_holder_pids "$lockfile"
    [ -f "$meta" ] && jq -r '.pid // empty' "$meta" 2>/dev/null
  } 2>/dev/null | grep -E '^[0-9]+$' | sort -u | while read -r pid; do
    # Only pids that are actually alive: the lock file and the metadata both
    # name the run that took the slot, which is usually the thing we just
    # killed, and reporting it again as a "survivor" is noise.
    kill -0 "$pid" 2>/dev/null && printf '%s\n' "$pid"
  done
  return 0
}

slots_to_reset() {
  local n=1 f slot
  if [ -n "$TARGET_SLOT" ]; then
    echo "$TARGET_SLOT"
    return 0
  fi
  while [ "$n" -le "${BOT_MAX_CONCURRENT_RUNS:-1}" ]; do echo "$n"; n=$((n + 1)); done
  # Slots from a time when maxConcurrentRuns was higher still need clearing.
  for f in "$SCRIPT_DIR"/.run.slot-*.lock; do
    [ -e "$f" ] || continue
    slot="${f##*/.run.slot-}"; slot="${slot%.lock}"
    case "$slot" in ''|*[!0-9]*) continue ;; esac
    [ "$slot" -gt "${BOT_MAX_CONCURRENT_RUNS:-1}" ] && echo "$slot"
  done
}

echo "=== Emergency Reset ==="
if [ "$STALE_ONLY" = true ]; then
  echo "(--stale: only orphaned slots will be cleared)"
fi

RESET_ANY=false
LEFT_RUNNING=false

for slot in $(slots_to_reset | sort -n | uniq); do
  LOCKFILE=$(bot_slot_lockfile "$SCRIPT_DIR" "$slot")
  META=$(bot_slot_meta_file "$SCRIPT_DIR" "$slot")
  RUN_STATE=$(bot_slot_run_state_file "$SCRIPT_DIR" "$slot")

  if bot_lock_probe "$LOCKFILE"; then
    STATE="IDLE"
  elif bot_lock_is_orphaned "$LOCKFILE"; then
    STATE="ORPHANED"
  else
    STATE="RUNNING"
  fi

  if [ "$STALE_ONLY" = true ] && [ "$STATE" = "RUNNING" ]; then
    echo ""
    echo "Slot $slot: RUNNING (pid $(bot_lock_owner_pid "$LOCKFILE")) — left alone."
    LEFT_RUNNING=true
    continue
  fi
  # --stale is for clearing wreckage; an idle slot with nothing left behind
  # is not wreckage, so say nothing about it.
  if [ "$STALE_ONLY" = true ] && [ "$STATE" = "IDLE" ] && [ ! -f "$META" ]; then
    continue
  fi

  echo ""
  echo "--- Slot $slot ($STATE) ---"

  if [ "$STATE" != "IDLE" ]; then
    PIDS=$(slot_pids "$LOCKFILE" "$META")
    if [ -n "$PIDS" ]; then
      echo "Killing process tree for: $(echo "$PIDS" | tr '\n' ' ')"
      for pid in $PIDS; do kill_tree TERM "$pid"; done
      sleep 2
      SURVIVORS=$(slot_pids "$LOCKFILE" "$META")
      if [ -n "$SURVIVORS" ]; then
        echo "Force-killing survivors: $(echo "$SURVIVORS" | tr '\n' ' ')"
        for pid in $SURVIVORS; do kill_tree KILL "$pid"; done
        sleep 1
      fi
    fi
    if bot_lock_probe "$LOCKFILE"; then
      echo "Lock released."
    else
      echo "WARNING: slot $slot is still locked. Something has the lock file open:" >&2
      bot_lock_holder_pids "$LOCKFILE" | sed 's/^/  pid /' >&2
    fi
  fi

  # The mkdir backend's directory is the one piece of lock state that can
  # survive its owner; drop it once the owner is confirmed gone.
  if [ -d "$LOCKFILE.d" ] && ! bot_lock_is_orphaned "$LOCKFILE"; then
    rm -rf "$LOCKFILE.d"
    echo "Removed stale lock directory."
  fi

  rm -f "$META"
  if [ -f "$RUN_STATE" ]; then
    "$SCRIPT_DIR/scripts/reset-run-state.sh" --state-file "$RUN_STATE"
  fi
  "$SCRIPT_DIR/scripts/claims.py" release --slot "$slot" 2>/dev/null || true
  RESET_ANY=true
done

if [ "$RESET_ANY" = false ]; then
  echo ""
  if [ "$LEFT_RUNNING" = true ]; then
    echo "Nothing to reset — every slot is running normally."
  else
    echo "Nothing to reset."
  fi
fi

# --- Sync target repo to upstream ---
if [ "$DO_SYNC" != true ]; then
  echo ""
  echo "=== Reset complete (sync skipped). ==="
  exit 0
fi

# Never touch the shared checkout while another run could be using it.
STILL_LIVE=""
n=1
while [ "$n" -le "${BOT_MAX_CONCURRENT_RUNS:-1}" ]; do
  bot_lock_probe "$(bot_slot_lockfile "$SCRIPT_DIR" "$n")" || STILL_LIVE="$STILL_LIVE $n"
  n=$((n + 1))
done
if [ -n "$STILL_LIVE" ]; then
  echo ""
  echo "Skipping the target-repo sync — slot(s)$STILL_LIVE are still running."
  echo "=== Reset complete. ==="
  exit 0
fi

echo ""
echo "Syncing $GIT_REPO to upstream/$BOT_DEFAULT_BRANCH..."
if [ ! -e "$GIT_REPO/.git" ]; then
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
