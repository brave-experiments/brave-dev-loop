#!/bin/bash
# What is actually running in this bot directory?
#
# Answers the question the lock files cannot: a lock file is just an inode to
# lock and outlives every run, so its presence means nothing. This asks the
# kernel for each slot's lock and reports one of three states:
#
#   IDLE      the lock is free — nothing is running in that slot
#   RUNNING   a live run holds it (pid, story, and how long since it last
#             wrote to its iteration log, which is the signal for a hang)
#   ORPHANED  the kernel lock is held but the run that took it is gone: a
#             leftover child inherited the lock fd and outlived its parent.
#             ./scripts/reset-run.sh --slot N clears it.
#
# Usage: ./scripts/run-status.sh [--json]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/run-slots.sh"

JSON=false
[ "${1:-}" = "--json" ] && JSON=true

MAX_SLOTS="${BOT_MAX_CONCURRENT_RUNS:-1}"

# Seconds since a file was last written, or empty when it does not exist.
file_age_secs() {
  local f="$1" mtime now
  [ -f "$f" ] || return 0
  if mtime=$(stat -f %m "$f" 2>/dev/null); then :
  elif mtime=$(stat -c %Y "$f" 2>/dev/null); then :
  else return 0
  fi
  now=$(date +%s)
  printf '%s' "$((now - mtime))"
}

human_age() {
  local s="$1"
  [ -n "$s" ] || { printf -- '-'; return; }
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ]; then printf '%dm' "$((s / 60))"
  else printf '%dh%dm' "$((s / 3600))" "$(((s % 3600) / 60))"
  fi
}

# Slots to report: 1..max, plus any beyond that still holding a lock from a
# time when maxConcurrentRuns was higher.
slots_to_report() {
  local n=1 f slot
  while [ "$n" -le "$MAX_SLOTS" ]; do echo "$n"; n=$((n + 1)); done
  for f in "$BOT_DIR"/.run.slot-*.lock; do
    [ -e "$f" ] || continue
    slot="${f##*/.run.slot-}"; slot="${slot%.lock}"
    case "$slot" in ''|*[!0-9]*) continue ;; esac
    [ "$slot" -gt "$MAX_SLOTS" ] && echo "$slot"
  done
}

slot_row() {
  local slot="$1"
  local lockfile meta state owner story log run_id started age heartbeat_age
  lockfile=$(bot_slot_lockfile "$BOT_DIR" "$slot")
  meta=$(bot_slot_meta_file "$BOT_DIR" "$slot")

  if bot_lock_probe "$lockfile"; then
    state="IDLE"
  elif bot_lock_is_orphaned "$lockfile"; then
    state="ORPHANED"
  else
    state="RUNNING"
  fi

  owner=$(bot_lock_owner_pid "$lockfile")
  story=""; log=""; run_id=""; started=""
  if [ -f "$meta" ]; then
    story=$(jq -r '.storyId // empty' "$meta" 2>/dev/null || true)
    log=$(jq -r '.log // empty' "$meta" 2>/dev/null || true)
    run_id=$(jq -r '.runId // empty' "$meta" 2>/dev/null || true)
    started=$(jq -r '.startedAt // empty' "$meta" 2>/dev/null || true)
  fi
  age=$(file_age_secs "$log")
  heartbeat_age=$(file_age_secs "$meta")

  # An IDLE slot's metadata is a leftover from the last run — do not report it
  # as if it were current.
  if [ "$state" = "IDLE" ]; then
    story=""; log=""; run_id=""; started=""; age=""; owner=""
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$slot" "$state" "${owner:--}" "${story:--}" "${started:--}" \
    "${age:--}" "${heartbeat_age:--}" "${log:--}"
}

ROWS=$(for slot in $(slots_to_report | sort -n | uniq); do slot_row "$slot"; done)

if [ "$JSON" = true ]; then
  printf '%s\n' "$ROWS" | jq -R -s --arg backend "$(bot_lock_backend)" \
    --arg project "$BOT_PROJECT_NAME" --arg profile "${BOT_PROFILE:-}" \
    --argjson max "$MAX_SLOTS" '
    {project: $project, profile: $profile, lockBackend: $backend,
     maxConcurrentRuns: $max,
     slots: (split("\n") | map(select(length > 0) | split("\t") | {
       slot: (.[0] | tonumber),
       state: .[1],
       pid: (if .[2] == "-" then null else (.[2] | tonumber) end),
       storyId: (if .[3] == "-" then null else .[3] end),
       startedAt: (if .[4] == "-" then null else .[4] end),
       logAgeSeconds: (if .[5] == "-" then null else (.[5] | tonumber) end),
       heartbeatAgeSeconds: (if .[6] == "-" then null else (.[6] | tonumber) end),
       log: (if .[7] == "-" then null else .[7] end)
     }))}'
  exit 0
fi

echo "$BOT_PROJECT_NAME — profile ${BOT_PROFILE:-unset}, maxConcurrentRuns=$MAX_SLOTS"
# run.sh refuses to start on this; say so here rather than leaving the operator
# to wonder why nothing runs.
if MISMATCH=$(bot_profile_mismatch); then
  echo "Warning: $MISMATCH"
fi
case "$(bot_lock_backend)" in
  mkdir) echo "Lock backend: mkdir (no flock or python3 here — a killed run can leave a lock behind)" ;;
  *)     echo "Lock backend: $(bot_lock_backend) (kernel-held — a killed run releases its slot immediately)" ;;
esac
echo ""
printf '%-5s %-9s %-8s %-10s %-21s %-9s %s\n' SLOT STATE PID STORY STARTED LAST-LOG LOG
printf '%s\n' "$ROWS" | while IFS=$'\t' read -r slot state pid story started age hb log; do
  [ -n "$slot" ] || continue
  [ "$age" = "-" ] && age_h="-" || age_h=$(human_age "$age")
  printf '%-5s %-9s %-8s %-10s %-21s %-9s %s\n' \
    "$slot" "$state" "$pid" "$story" "$started" "$age_h" "$(basename "$log" 2>/dev/null || echo -)"
done
echo ""

# Advisory notes: hangs and orphans, the two states an operator has to act on.
printf '%s\n' "$ROWS" | while IFS=$'\t' read -r slot state pid story started age hb log; do
  [ -n "$slot" ] || continue
  if [ "$state" = "ORPHANED" ]; then
    echo "! slot $slot: lock held but its run (pid ${pid}) is gone — a leftover child still holds it."
    echo "    clear with: ./scripts/reset-run.sh --slot $slot"
  elif [ "$state" = "RUNNING" ] && [ "$age" != "-" ] && [ "$age" -gt 1800 ]; then
    echo "! slot $slot: no log output for $(human_age "$age") — possibly hung (iterations are capped at 2h)."
    echo "    inspect: tail -f $log      stop it: ./scripts/reset-run.sh --slot $slot"
  fi
done
