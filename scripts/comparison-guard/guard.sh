#!/bin/bash
# Shared part of the comparison-run PATH shims (see gh, git beside this file).
#
# A comparison run repeats a story's work so it can be compared against the base
# run's. It must not reach the outside world: a second pull request for the same
# story, or a duplicate issue comment, is worse than no comparison at all. The
# prompt says so, and this makes it true.
#
# The shim directory goes first on PATH, so `gh` and `git` here are what the
# agent gets. Reads pass straight through to the real binary; writes are refused
# with a message that says why, so a refusal reads as the rule it is rather than
# as a broken tool.
#
# BOT_COMPARISON_GUARD_ALLOW=issue-create re-allows `gh issue create` for the
# evaluator run, whose whole job is to file issues. Pull request writes and
# `git push` stay refused there too.

GUARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The real binary is the next one on PATH — found by walking PATH with this
# directory removed, since `command -v` would just find the shim again.
#
# PATH is split by trimming it entry by entry rather than by setting IFS: a
# global IFS changes how every later unquoted expansion in the shim splits.
guard_real_binary() {
  local tool="$1" rest="$PATH" dir candidate
  while [ -n "$rest" ]; do
    dir="${rest%%:*}"
    if [ "$dir" = "$rest" ]; then
      rest=""
    else
      rest="${rest#*:}"
    fi
    [ -n "$dir" ] || dir="."
    if [ "$(cd "$dir" 2>/dev/null && pwd)" = "$GUARD_DIR" ]; then
      continue
    fi
    candidate="$dir/$tool"
    if [ -x "$candidate" ] && [ ! -d "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

# Refuse a command, naming what was blocked and what to do instead.
guard_refuse() {
  local what="$1"
  echo "comparison-run guard: refused '$what'." >&2
  echo "  A comparison run stops before anything public: no push, no pull request, no issue," >&2
  echo "  no comment. Commit in the worktree and finish with a summary instead." >&2
  echo "  (Set by BOT_COMPARISON_RUN; see docs/comparison-runs.md.)" >&2
  exit 1
}

# Hand off to the real binary, or say plainly that it is missing.
guard_exec() {
  local tool="$1"
  shift
  local real
  if ! real="$(guard_real_binary "$tool")"; then
    echo "comparison-run guard: no real '$tool' found on PATH." >&2
    exit 127
  fi
  exec "$real" "$@"
}

# First argument that is not a flag, and the one after it: gh's command and
# subcommand. `gh pr create` is ("pr", "create").
guard_command_pair() {
  local first="" second=""
  local arg
  for arg in "$@"; do
    case "$arg" in
      -*) continue ;;
    esac
    if [ -z "$first" ]; then
      first="$arg"
    elif [ -z "$second" ]; then
      second="$arg"
      break
    fi
  done
  printf '%s %s' "$first" "$second"
}
