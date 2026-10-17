#!/bin/bash
# Parallel jobs: one ./run.sh that starts several runs of itself.
#
# bot.maxConcurrentRuns says how many run.sh instances may share this bot
# directory (docs/concurrent-runs.md); someone still has to start them.
# bot.concurrentJobs is the someone: with it above 1, ./run.sh starts that many
# copies of itself and waits for all of them. Each copy is an ordinary run that
# takes a run slot of its own, works its own stories, and does everything a run
# does, so the slots, claims and locks are the ones concurrent runs already use.
#
# The copies are told apart from the original by BOT_RUN_JOB, which is what
# stops each of them starting more.
#
# Sourced by run.sh after lib/load-config.sh.

# Is bot.concurrentJobs usable with this many run slots? 0 = yes, 1 = no (reason
# on stderr). A job that finds every slot taken exits without working anything,
# so more jobs than slots is a setting that cannot do what it says.
bot_validate_concurrent_jobs() {
  local jobs="$1" slots="$2"
  case "$jobs" in
    ''|*[!0-9]*)
      echo "Error: bot.concurrentJobs must be a positive integer (got '$jobs')" >&2
      return 1 ;;
  esac
  if [ "$jobs" -lt 1 ]; then
    echo "Error: bot.concurrentJobs must be at least 1" >&2
    return 1
  fi
  if [ "$jobs" -gt "$slots" ]; then
    echo "Error: bot.concurrentJobs is $jobs, but bot.maxConcurrentRuns is $slots." >&2
    echo "  Each job takes a run slot, so $((jobs - slots)) of them would find every slot taken." >&2
    echo "  Raise bot.maxConcurrentRuns to at least $jobs in config.json, or lower bot.concurrentJobs." >&2
    return 1
  fi
  return 0
}

# Every line from stdin, behind a prefix. A line the writer left unterminated is
# still a line.
_bot_prefix_lines() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s%s\n' "$1" "$line"
  done
}

# Signal a process and everything under it. The process goes first, with its
# children listed beforehand because they are reparented once it is gone.
#
# That order is what stops a job. A job's shell holds a TERM until the command it
# is running ends, then exits; signalled after its agent, it would have gone on to
# resume the agent in the gap between the two.
_bot_kill_tree() {
  local sig="$1" pid="$2" children child
  children=$(pgrep -P "$pid" 2>/dev/null) || true
  kill -"$sig" "$pid" 2>/dev/null || true
  for child in $children; do
    _bot_kill_tree "$sig" "$child"
  done
}

_BOT_JOB_PIDS=()
_BOT_JOB_DIR=""
_BOT_JOB_SLEEP_PID=""
# How long a job gets to clean up after TERM before it is killed outright.
BOT_JOB_STOP_SECONDS="${BOT_JOB_STOP_SECONDS:-30}"
# How long after one job starts the next one does. Jobs that start together all
# run the same start-up (target-repo sync, PRD sync, label sweep, worktree
# cleanup) and select a story in the same instant; the gap lets the first finish
# what the others would repeat or skip.
BOT_JOB_START_DELAY_SECONDS="${BOT_JOB_START_DELAY_SECONDS:-10}"

# The operator pressed Ctrl+C, or something sent TERM: stop every job the way it
# would have stopped if it were the only run, then leave.
_bot_jobs_stop() {
  local pid waited=0 alive
  trap '' INT TERM HUP
  echo "" >&2
  echo "Stopping ${#_BOT_JOB_PIDS[@]} jobs..." >&2
  [ -z "$_BOT_JOB_SLEEP_PID" ] || kill "$_BOT_JOB_SLEEP_PID" 2>/dev/null || true
  for pid in "${_BOT_JOB_PIDS[@]}"; do
    _bot_kill_tree TERM "$pid"
  done
  while [ "$waited" -lt "$BOT_JOB_STOP_SECONDS" ]; do
    alive=false
    for pid in "${_BOT_JOB_PIDS[@]}"; do
      kill -0 "$pid" 2>/dev/null && alive=true
    done
    [ "$alive" = true ] || break
    sleep 1
    waited=$((waited + 1))
  done
  for pid in "${_BOT_JOB_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "Job $pid did not stop within ${BOT_JOB_STOP_SECONDS}s; killing it." >&2
      _bot_kill_tree KILL "$pid"
    fi
  done
  [ -z "$_BOT_JOB_DIR" ] || rm -rf "$_BOT_JOB_DIR"
  exit 130
}

# Start <jobs> copies of <script> with the arguments that follow, print what each
# says behind "[job N] ", and exit with the worst status any of them returned.
#
#   bot_run_parallel_jobs <jobs> <script> [args...]
#
# Never returns. The copies' output goes through a pipe each, so a line is never
# split by another job's; their lines interleave, which the prefix sorts out.
bot_run_parallel_jobs() {
  local jobs="$1" script="$2" i pid rc worst=0
  shift 2
  local prefixers=()

  _BOT_JOB_DIR=$(mktemp -d "${TMPDIR:-/tmp}/run-jobs.XXXXXX") || exit 1
  _BOT_JOB_PIDS=()
  trap _bot_jobs_stop INT TERM HUP

  echo "Starting $jobs parallel jobs (bot.concurrentJobs). Each takes a run slot of its own; their lines are prefixed [job N]."
  if [ "$jobs" -gt 1 ] && [ "$BOT_JOB_START_DELAY_SECONDS" -gt 0 ]; then
    echo "Each job starts ${BOT_JOB_START_DELAY_SECONDS}s after the one before it."
  fi
  for i in $(seq 1 "$jobs"); do
    if [ "$i" -gt 1 ] && [ "$BOT_JOB_START_DELAY_SECONDS" -gt 0 ]; then
      # Backgrounded and waited on, so a TERM ends the gap at once; bash holds a
      # trap until a foreground command finishes.
      sleep "$BOT_JOB_START_DELAY_SECONDS" &
      _BOT_JOB_SLEEP_PID=$!
      wait "$_BOT_JOB_SLEEP_PID" 2>/dev/null || true
      _BOT_JOB_SLEEP_PID=""
    fi
    mkfifo "$_BOT_JOB_DIR/job-$i"
    _bot_prefix_lines "[job $i] " < "$_BOT_JOB_DIR/job-$i" &
    prefixers+=("$!")
    # Several jobs titling one terminal tab would leave it showing whichever
    # wrote last.
    BOT_RUN_JOB="$i" BOT_TITLE_TTY=/dev/null "$script" "$@" > "$_BOT_JOB_DIR/job-$i" 2>&1 &
    _BOT_JOB_PIDS+=("$!")
  done

  for pid in "${_BOT_JOB_PIDS[@]}"; do
    rc=0
    wait "$pid" || rc=$?
    [ "$rc" -le "$worst" ] || worst=$rc
  done
  for pid in "${prefixers[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
  rm -rf "$_BOT_JOB_DIR"
  trap - INT TERM HUP
  exit "$worst"
}
