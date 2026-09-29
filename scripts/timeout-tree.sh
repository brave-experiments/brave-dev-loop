#!/bin/bash
# Like timeout, but kills the entire process tree (not just the direct child).
#
# Usage: timeout-tree.sh [--quiet <seconds> <glob>...] <seconds> <command...>
#
# Problem: GNU timeout only kills the direct child's process group. If the child
# spawns subprocesses in new process groups (e.g., Claude's Bash tool), those
# survive and become zombie orphans reparented to init.
#
# Solution: Recursively kill all descendant processes by walking the process tree.
# We avoid setsid because it creates a new session with no controlling terminal,
# which breaks Ink's raw mode check even in --print mode.
#
# --quiet also kills the tree once nothing matching a glob has changed for that
# many seconds, and exits 75 (EX_TEMPFAIL) so the caller can tell a stall from
# the time limit. A glob that matches nothing yet counts as no change. Each glob
# is expanded afresh on every poll, and a directory counts everything under it.
# The globs stop at the first argument that is a plain number: the time limit.

set -e

QUIET_SECS=""
QUIET_GLOBS=()
if [ "$1" = "--quiet" ]; then
  QUIET_SECS="$2"
  shift 2
  while [ $# -gt 0 ] && ! [[ "$1" =~ ^[0-9]+$ ]]; do
    QUIET_GLOBS+=("$1")
    shift
  done
fi
QUIET_EXIT=75
POLL_SECS="${TIMEOUT_TREE_POLL_SECS:-30}"

if [ $# -lt 2 ]; then
  echo "Usage: timeout-tree.sh [--quiet <seconds> <glob>...] <seconds> <command...>"
  exit 1
fi

TIMEOUT_SECS="$1"
shift

# Recursively kill a process and all its descendants (leaf-first)
kill_tree() {
  local sig="${1:-TERM}"
  local pid="$2"
  # Get children before killing the parent
  local children
  children=$(pgrep -P "$pid" 2>/dev/null) || true
  for child in $children; do
    kill_tree "$sig" "$child"
  done
  kill -"$sig" "$pid" 2>/dev/null || true
}

STATE_DIR=$(mktemp -d)
touch "$STATE_DIR/stamp"

# Whether anything a glob matches changed since the stamp. IFS is a newline only
# so a path with a space in it survives the unquoted expansion that globs it.
changed() {
  local glob path IFS=$'\n'
  for glob in "${QUIET_GLOBS[@]}"; do
    for path in $glob; do
      [ -e "$path" ] || continue
      if [ -n "$(find "$path" -newer "$STATE_DIR/stamp" -print 2>/dev/null | head -n 1)" ]; then
        return 0
      fi
    done
  done
  return 1
}

# Run command in background (inherits the current session/TTY)
"$@" &
CMD_PID=$!

# Background watchdog: kill the process tree after timeout
(
  if [ -n "$QUIET_SECS" ]; then
    started=$(date +%s)
    last=$started
    while :; do
      now=$(date +%s)
      [ $((now - started)) -lt "$TIMEOUT_SECS" ] || break
      touch "$STATE_DIR/next"
      if changed; then
        mv "$STATE_DIR/next" "$STATE_DIR/stamp"
        last=$now
      elif [ $((now - last)) -ge "$QUIET_SECS" ]; then
        echo quiet > "$STATE_DIR/reason"
        break
      fi
      wait_for=$((TIMEOUT_SECS - (now - started)))
      [ "$wait_for" -le "$POLL_SECS" ] || wait_for=$POLL_SECS
      sleep "$wait_for"
    done
  else
    sleep "$TIMEOUT_SECS"
  fi
  kill_tree TERM "$CMD_PID"
  sleep 5
  kill_tree KILL "$CMD_PID"
) >/dev/null 2>&1 &
WATCHDOG_PID=$!

# Wait for the command to finish. Under set -e a bare wait on a failed command
# would end this script here, leaving the watchdog to kill whatever next takes
# the pid.
EXIT_CODE=0
wait "$CMD_PID" 2>/dev/null || EXIT_CODE=$?

# Cancel the watchdog and its children. Stopped first: killed leaf-first while
# polling, it would start its next command in between, and a sleep started then
# outlives it. Its output goes nowhere, so nothing it leaves holds the caller's
# pipe open: run.sh's tee would wait out that sleep.
kill -STOP "$WATCHDOG_PID" 2>/dev/null || true
kill_tree KILL "$WATCHDOG_PID"
wait "$WATCHDOG_PID" 2>/dev/null || true

if [ -f "$STATE_DIR/reason" ]; then
  rm -rf "$STATE_DIR"
  echo "Job stopped after ${QUIET_SECS}s with nothing written"
  exit "$QUIET_EXIT"
fi
rm -rf "$STATE_DIR"

if [ $EXIT_CODE -eq 137 ] || [ $EXIT_CODE -eq 143 ]; then
  echo "Job killed after exceeding timeout of ${TIMEOUT_SECS}s"
fi

exit $EXIT_CODE
