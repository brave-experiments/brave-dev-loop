#!/bin/bash
# Single-instance locking, with a fallback for systems without flock.
#
# flock is not installed by default on macOS. The previous inline
#   flock -n 200 || { echo "Another run is in progress"; exit 0; }
# could not tell "lock held" from "flock not found" — a missing binary exits
# 127, took the || branch, and every run and cron job exited 0 having done
# nothing, reporting the same message as a genuine concurrent run.
#
# Usage:
#   source .../lib/lock.sh
#   bot_acquire_lock "$LOCKFILE" || { echo "already running"; exit 0; }
#
# Returns 0 when the lock is held by this process, 1 when another live process
# holds it. Anything else is fatal and reported, never silently swallowed.

# fd 200 is fixed rather than dynamic: `exec {fd}>` needs bash 4, and macOS
# ships bash 3.2.
BOT_LOCK_FD=200
BOT_LOCK_DIR=""

bot_release_lock() {
  if [ -n "$BOT_LOCK_DIR" ] && [ -d "$BOT_LOCK_DIR" ]; then
    # Only clear a lock this process owns; a stale-cleanup race could otherwise
    # delete a lock another run has just taken.
    if [ "$(cat "$BOT_LOCK_DIR/pid" 2>/dev/null)" = "$$" ]; then
      rm -rf "$BOT_LOCK_DIR"
    fi
  fi
  BOT_LOCK_DIR=""
}

_bot_lock_holder_alive() {
  local pid="$1"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

# Split out so tests can exercise both paths on any machine.
_bot_have_flock() {
  command -v flock >/dev/null 2>&1
}

bot_acquire_lock() {
  local lockfile="$1"
  [ -n "$lockfile" ] || { echo "bot_acquire_lock: no lockfile given" >&2; return 2; }

  if _bot_have_flock; then
    eval "exec $BOT_LOCK_FD>\"\$lockfile\"" || {
      echo "Could not open lock file $lockfile" >&2
      return 2
    }
    flock -n "$BOT_LOCK_FD"
    return $?
  fi

  # Fallback: mkdir is atomic on every POSIX filesystem.
  local dir="$lockfile.d"
  if mkdir "$dir" 2>/dev/null; then
    echo $$ > "$dir/pid"
    BOT_LOCK_DIR="$dir"
    return 0
  fi

  # Directory exists. Either a live holder, or a lock left behind by a process
  # that was killed before it could clean up.
  local holder
  holder=$(cat "$dir/pid" 2>/dev/null || echo "")
  if _bot_lock_holder_alive "$holder"; then
    return 1
  fi

  echo "Clearing stale lock at $dir (pid ${holder:-unknown} is gone)" >&2
  rm -rf "$dir"
  if mkdir "$dir" 2>/dev/null; then
    echo $$ > "$dir/pid"
    BOT_LOCK_DIR="$dir"
    return 0
  fi
  return 1
}
