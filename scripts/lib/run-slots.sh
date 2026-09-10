#!/bin/bash
# Run slots: the mechanism that lets several run.sh instances share one bot
# directory.
#
# A slot is a number from 1 to bot.maxConcurrentRuns. Holding slot N means
# holding its lock (see lib/lock.sh) for the life of the run; everything else
# a run touches that cannot be shared — its run-state file, its iteration logs,
# its story claim — is keyed by that number.
#
# Slot 1 keeps every path it had before slots existed, so a deployment that
# never raises maxConcurrentRuns above 1 sees no change at all.
#
# The lock is the only source of truth about what is running. The metadata in
# data/runs/slot-N.json is written for humans and for run-status.sh; it is
# never consulted to decide whether a slot is free, because a killed run
# leaves it behind while the kernel drops the lock instantly.

_bot_slots_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lock.sh
source "$_bot_slots_lib_dir/lock.sh"

BOT_RUN_SLOT=""
BOT_RUN_SLOT_META=""
# Filled in by bot_acquire_run_slot when every slot is busy: one line per slot
# saying who holds it, so "all slots busy" is never a dead end.
BOT_SLOT_BUSY_REPORT=""

bot_slot_lockfile() {
  local bot_dir="$1" slot="$2"
  if [ "$slot" = "1" ]; then
    printf '%s\n' "$bot_dir/.run.lock"
  else
    printf '%s\n' "$bot_dir/.run.slot-$slot.lock"
  fi
}

bot_slot_meta_file() {
  local bot_dir="$1" slot="$2"
  printf '%s\n' "$bot_dir/data/runs/slot-$slot.json"
}

bot_slot_run_state_file() {
  local bot_dir="$1" slot="$2"
  if [ "$slot" = "1" ]; then
    printf '%s\n' "$bot_dir/data/run-state.json"
  else
    printf '%s\n' "$bot_dir/data/run-state.slot-$slot.json"
  fi
}

# Merge key/value pairs into this slot's metadata file. Values are passed to jq
# as strings; a key ending in ':num' is written as a number instead.
bot_slot_meta_set() {
  local meta="$BOT_RUN_SLOT_META"
  [ -n "$meta" ] || return 0
  mkdir -p "$(dirname "$meta")"
  [ -f "$meta" ] || printf '{}\n' > "$meta"

  local filter='.' ; local -a jq_args=()
  local i=0 pair key value
  for pair in "$@"; do
    key="${pair%%=*}"
    value="${pair#*=}"
    i=$((i + 1))
    if [ "${key%:num}" != "$key" ]; then
      key="${key%:num}"
      filter="$filter | .[\$k$i] = (\$v$i | tonumber? // \$v$i)"
    else
      filter="$filter | .[\$k$i] = \$v$i"
    fi
    jq_args+=(--arg "k$i" "$key" --arg "v$i" "$value")
  done

  local tmp
  tmp=$(mktemp "$(dirname "$meta")/.slot-meta.XXXXXX") || return 0
  if jq "${jq_args[@]}" "$filter" "$meta" > "$tmp" 2>/dev/null; then
    mv "$tmp" "$meta"
  else
    rm -f "$tmp"
  fi
}

bot_slot_heartbeat() {
  bot_slot_meta_set "heartbeat=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

bot_slot_meta_clear() {
  [ -n "$BOT_RUN_SLOT_META" ] && rm -f "$BOT_RUN_SLOT_META"
  BOT_RUN_SLOT_META=""
}

# Is this concurrency setting allowed for this profile?
# 0 = fine, 1 = refuse (reason on stderr).
#
# Running two agents against one working tree destroys work, so anything above
# 1 requires a profile that gives every story its own git worktree.
bot_validate_concurrency() {
  local max="$1" profile="$2" worktrees="$3"

  case "$max" in
    ''|*[!0-9]*)
      echo "Error: bot.maxConcurrentRuns must be a positive integer (got '$max')" >&2
      return 1 ;;
  esac
  if [ "$max" -lt 1 ]; then
    echo "Error: bot.maxConcurrentRuns must be at least 1" >&2
    return 1
  fi
  if [ "$max" -gt 1 ] && [ "$worktrees" != true ]; then
    echo "Error: bot.maxConcurrentRuns is $max, but profile '$profile' does not use worktrees." >&2
    echo "  Concurrent runs share one checkout under this profile and would overwrite each other." >&2
    echo "  Either set bot.maxConcurrentRuns to 1 in config.json, or move the profile to" >&2
    echo "  per-story worktrees and set \"worktrees\": true in projects/$profile/profile.json." >&2
    return 1
  fi
  return 0
}

# Take the first free slot. Sets BOT_RUN_SLOT and BOT_RUN_SLOT_META on success.
# 0 = got a slot, 1 = every slot is busy, 2 = error.
bot_acquire_run_slot() {
  local bot_dir="$1" max="${2:-1}"
  local slot lockfile rc

  case "$max" in
    ''|*[!0-9]*) echo "bot_acquire_run_slot: bad slot count '$max'" >&2; return 2 ;;
  esac
  [ "$max" -ge 1 ] || { echo "bot_acquire_run_slot: slot count must be >= 1" >&2; return 2; }

  slot=1
  while [ "$slot" -le "$max" ]; do
    lockfile=$(bot_slot_lockfile "$bot_dir" "$slot")
    # `|| rc=$?` and not a bare call: under `set -e` a function returning
    # non-zero exits the caller before it can read $?, which is how "all slots
    # busy" used to become a silent exit.
    rc=0
    bot_acquire_lock "$lockfile" || rc=$?
    case $rc in
      0)
        BOT_RUN_SLOT="$slot"
        BOT_RUN_SLOT_META=$(bot_slot_meta_file "$bot_dir" "$slot")
        mkdir -p "$(dirname "$BOT_RUN_SLOT_META")"
        # A metadata file left by a killed run is meaningless — start clean.
        printf '{}\n' > "$BOT_RUN_SLOT_META"
        bot_slot_meta_set \
          "slot:num=$slot" \
          "pid:num=$$" \
          "lockFile=$lockfile" \
          "lockBackend=$BOT_LOCK_BACKEND" \
          "startedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        bot_slot_heartbeat
        return 0
        ;;
      1)
        local owner note
        owner=$(bot_lock_owner_pid "$lockfile")
        if bot_lock_is_orphaned "$lockfile"; then
          note="ORPHANED — its run (pid ${owner:-unknown}) is gone but a leftover child still holds the lock. Clear it with: ./scripts/reset-run.sh --slot $slot"
        else
          note="held by pid ${owner:-unknown}$(_bot_slot_meta_note "$bot_dir" "$slot")"
        fi
        BOT_SLOT_BUSY_REPORT="$BOT_SLOT_BUSY_REPORT  slot $slot: $note
"
        ;;
      *) return 2 ;;
    esac
    slot=$((slot + 1))
  done
  return 1
}

# " (US-004, started ...)" for the busy report, or nothing when there is no
# metadata to read. Advisory only — never used to decide if a slot is free.
_bot_slot_meta_note() {
  local meta story started
  meta=$(bot_slot_meta_file "$1" "$2")
  [ -f "$meta" ] || return 0
  story=$(jq -r '.storyId // empty' "$meta" 2>/dev/null)
  started=$(jq -r '.startedAt // empty' "$meta" 2>/dev/null)
  [ -n "$story$started" ] || return 0
  printf ' (%s%s%s)' "${story:-no story yet}" "${started:+, since }" "$started"
}

# 0 = slot free, 1 = a run holds it, 2 = cannot tell.
bot_slot_is_free() {
  bot_lock_probe "$(bot_slot_lockfile "$1" "$2")"
}
