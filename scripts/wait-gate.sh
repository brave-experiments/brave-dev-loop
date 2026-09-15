#!/bin/bash
# Run presubmit gates and block until they finish, instead of sleeping.
#
# Usage:
#   ./scripts/wait-gate.sh run   [--dir REPO] [--timeout SECS] <gate> [<gate>...]
#   ./scripts/wait-gate.sh start [--dir REPO] [--log-dir DIR]   <gate> [<gate>...]
#   ./scripts/wait-gate.sh wait  [--timeout SECS] <log-dir>
#
# Each <gate> is a shell command ("make check", "cargo test --all --locked").
# Gates given in one invocation run concurrently, each into its own log.
#
#   run    launch the gates and block until they all finish
#   start  launch them and return at once, printing the log directory
#   wait   block on a directory a previous `start` printed
#
# `start` + `wait` is how a gate overlaps with something else that takes
# minutes — a code review, say. `run` is the rest of the time.
#
# The point of `wait` is that it polls the gates every couple of seconds
# rather than sleeping a fixed interval: it returns when the gate returns.
# A `sleep 540` in its place costs whatever is left of the 540s once the gate
# is done, and a second turn every time the guess was short.
#
# Exit status: 0 every gate passed, 1 one of them failed, 2 the timeout
# expired with a gate still running (the gates keep going; `wait` again).
#
# Verdicts are read from each log's own `EXIT=` line, never from the status of
# the pipeline that printed it — see docs/testing-requirements.md.

set -e

TIMEOUT=570
POLL_SECONDS=2
DIR="."
LOG_DIR=""
FAIL_CONTEXT=20

usage() {
  sed -n '2,28p' "$0"
  exit 1
}

MODE="${1:-}"; shift || true
case "$MODE" in
  run|start|wait) ;;
  *) usage ;;
esac

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --dir=*) DIR="${1#--dir=}"; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --timeout=*) TIMEOUT="${1#--timeout=}"; shift ;;
    --log-dir) LOG_DIR="$2"; shift 2 ;;
    --log-dir=*) LOG_DIR="${1#--log-dir=}"; shift ;;
    --) shift; break ;;
    -*) echo "Unknown option: $1" >&2; exit 1 ;;
    *) break ;;
  esac
done

case "$TIMEOUT" in
  ''|*[!0-9]*) echo "Error: --timeout takes whole seconds, got '$TIMEOUT'" >&2; exit 1 ;;
esac

# Every slot on this machine writes here, so the default name carries the pid
# and the clock: a shared one would have two runs reading each other's logs.
default_log_dir() {
  printf '%s/brave-dev-loop-gates.%s.%s.%s' \
    "${TMPDIR:-/tmp}" "$$" "$(date +%s)" "${RANDOM}${RANDOM}"
}

launch() {
  [ $# -gt 0 ] || usage
  if [ ! -d "$DIR" ]; then
    echo "Error: '$DIR' is not a directory" >&2
    exit 1
  fi
  [ -n "$LOG_DIR" ] || LOG_DIR="$(default_log_dir)"
  mkdir -p "$LOG_DIR"
  local n=0
  for gate in "$@"; do
    n=$((n + 1))
    printf '%s' "$gate" > "$LOG_DIR/$n.cmd"
    date +%s > "$LOG_DIR/$n.start"
    # The echo goes inside the redirect so the log holds the gate's own status.
    # Outside it, the exit code belongs to the echo and every gate looks green.
    # `set +e` because a failing gate under errexit would abort the subshell
    # before the echo runs, and a gate with no EXIT= line reads as killed.
    #
    # The subshell's own stdout goes to /dev/null so the gate lets go of the
    # descriptor it inherited. Left holding it, `start` run inside a command
    # substitution blocks until the gate finishes, which is the one thing
    # `start` exists not to do.
    (
      set +e
      cd "$DIR" || exit
      { bash -c "$gate"; echo "EXIT=$?"; } > "$LOG_DIR/$n.log" 2>&1 < /dev/null
      date +%s > "$LOG_DIR/$n.end"
    ) > /dev/null 2>&1 < /dev/null &
    echo $! > "$LOG_DIR/$n.pid"
  done
  echo "$n" > "$LOG_DIR/count"
}

# Prints "running", or the gate's exit code, or "killed" when the process is
# gone without having written one.
gate_state() {
  local dir="$1" n="$2" code pid
  code=$(sed -n 's/^EXIT=\([0-9]*\)$/\1/p' "$dir/$n.log" 2>/dev/null | tail -1)
  if [ -n "$code" ]; then
    echo "$code"
    return
  fi
  pid=$(cat "$dir/$n.pid" 2>/dev/null || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo running
  else
    echo killed
  fi
}

human_elapsed() {
  local secs="$1"
  printf '%dm%02ds' $((secs / 60)) $((secs % 60))
}

report() {
  local dir="$1" count="$2" failed=0 running=0 n=1
  while [ "$n" -le "$count" ]; do
    local state cmd started finished elapsed verdict
    state=$(gate_state "$dir" "$n")
    cmd=$(cat "$dir/$n.cmd" 2>/dev/null || echo '?')
    started=$(cat "$dir/$n.start" 2>/dev/null || date +%s)
    # A finished gate stamped its own end time, so waiting later on a gate that
    # is already done still reports how long the gate itself took.
    finished=$(cat "$dir/$n.end" 2>/dev/null || date +%s)
    elapsed=$(human_elapsed $(( finished - started )))
    case "$state" in
      running) verdict="RUNNING"; running=$((running + 1)) ;;
      0)       verdict="PASS" ;;
      killed)  verdict="KILLED"; failed=$((failed + 1)) ;;
      *)       verdict="FAIL exit=$state"; failed=$((failed + 1)) ;;
    esac
    echo "GATE $n $verdict  $elapsed  [$cmd]  $dir/$n.log"
    if [ "$verdict" != "PASS" ] && [ "$verdict" != "RUNNING" ]; then
      # The failing lines, so reading the verdict does not cost another grep.
      local hits
      hits=$(grep -nE 'error(\[|:)|^failures:|test result: FAILED|FAILED|Error:|panicked at' \
        "$dir/$n.log" 2>/dev/null | head -"$FAIL_CONTEXT" || true)
      [ -n "$hits" ] || hits=$(tail -"$FAIL_CONTEXT" "$dir/$n.log" 2>/dev/null || true)
      [ -n "$hits" ] && echo "$hits" | sed 's/^/    /'
    fi
    n=$((n + 1))
  done
  if [ "$running" -gt 0 ]; then
    echo "TIMEOUT after ${TIMEOUT}s with $running gate(s) still running."
    echo "They are still going. Block on them again with:"
    echo "  ./scripts/wait-gate.sh wait --timeout $TIMEOUT $dir"
    return 2
  fi
  [ "$failed" -eq 0 ] || return 1
  return 0
}

block() {
  local dir="$1" count deadline
  if [ ! -f "$dir/count" ]; then
    echo "Error: '$dir' is not a wait-gate log directory" >&2
    exit 1
  fi
  count=$(cat "$dir/count")
  deadline=$(( $(date +%s) + TIMEOUT ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local n=1 pending=0
    while [ "$n" -le "$count" ]; do
      [ "$(gate_state "$dir" "$n")" = running ] && pending=$((pending + 1))
      n=$((n + 1))
    done
    [ "$pending" -eq 0 ] && break
    sleep "$POLL_SECONDS"
  done
  set +e
  report "$dir" "$count"
  exit $?
}

case "$MODE" in
  start)
    launch "$@"
    echo "$LOG_DIR"
    ;;
  run)
    launch "$@"
    block "$LOG_DIR"
    ;;
  wait)
    [ $# -eq 1 ] || usage
    block "$1"
    ;;
esac
