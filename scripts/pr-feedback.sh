#!/bin/bash
# One scheduled pass of the PR-feedback job: if a bot PR has a request for changes
# (or red CI, unless the profile turns that off), run ONE agent iteration on it in a
# throwaway worktree, re-request review from whoever rejected it, and remove the
# worktree.
#
#   ./scripts/pr-feedback.sh [--dry-run]
#
# Everything that decides whether to start the agent is pr-feedback.py: plain gh
# reads and a cache under ~/.brave-dev-loop, no model. This script runs only after
# that says yes, and it starts at most one agent per pass. The cron entry runs the
# same gate first (scripts/pr-feedback.py gate), so an idle five minutes costs one
# `gh pr list` and nothing else.
#
# The agent is launched the way run.sh launches an iteration (same agent, model and
# binary from config.json, via lib/agent-launch.sh), with a prompt that points it at
# docs/workflow-pushed.md's Review Response Workflow.
#
# Exit 0 when nothing was due or the pass finished, whatever the agent did: the
# outcome is in the cache, and cron has nothing useful to do with a failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/agent-launch.sh"

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

FB="$SCRIPT_DIR/pr-feedback.py"

TARGET=$(python3 "$FB" next -v) || {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] pr-feedback: nothing to do."
  exit 0
}
PR=$(jq -r .number <<< "$TARGET")
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] pr-feedback: PR #$PR - $(jq -r .why <<< "$TARGET")"
if [ "$DRY_RUN" = true ]; then
  echo "(dry run: no worktree, no agent)"
  exit 0
fi

# Recorded before any work, so a killed pass still counts as an attempt and the
# next tick does not retry at once.
AT=$(python3 "$FB" begin <<< "$TARGET" | jq -r .at) || exit 0

WORKTREE=""
CREATED=false
AGENT_RC=0
OUTCOME=""
finish() {
  # A worktree this pass did not create is someone's: leave it.
  if [ "$CREATED" = true ] && [ -n "$WORKTREE" ]; then
    python3 "$FB" cleanup --path "$WORKTREE" || true
  fi
  if [ -n "$OUTCOME" ]; then
    python3 "$FB" finish --pr "$PR" --at "$AT" --outcome "$OUTCOME" || true
  else
    python3 "$FB" finish --pr "$PR" --at "$AT" --agent-rc "$AGENT_RC" || true
  fi
}
trap finish EXIT
trap 'AGENT_RC=143; exit 143' TERM INT HUP

WT_JSON=$(python3 "$FB" worktree --pr "$PR")
WT_RC=$?
if [ "$WT_RC" -eq 3 ]; then
  OUTCOME=busy
  exit 0
elif [ "$WT_RC" -ne 0 ]; then
  OUTCOME=setup-failed
  exit 0
fi
WORKTREE=$(jq -r .path <<< "$WT_JSON")
CREATED=$(jq -r .created <<< "$WT_JSON")

LOG_DIR="${BOT_LOG_DIR:-$BOT_DIR/logs}/pr-feedback"
mkdir -p "$LOG_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$LOG_DIR/pr-$PR-$STAMP.log"
PROMPT_FILE=$(mktemp "${TMPDIR:-/tmp}/pr-feedback-prompt.XXXXXX")
FINAL_MSG=$(mktemp "${TMPDIR:-/tmp}/pr-feedback-final.XXXXXX")
trap 'rm -f "$PROMPT_FILE" "$FINAL_MSG"; finish' EXIT

python3 "$FB" prompt --pr "$PR" --worktree "$WORKTREE" <<< "$TARGET" > "$PROMPT_FILE" || {
  OUTCOME=setup-failed
  exit 0
}

case "${BOT_AGENT:-claude}" in
  claude) MODEL="${BOT_CLAUDE_MODEL:-}"; BIN="${BOT_CLAUDE_BIN:-claude}" ;;
  codex) MODEL="${BOT_CODEX_MODEL:-}"; BIN="${BOT_CODEX_BIN:-codex}" ;;
  cursor) MODEL="${BOT_CURSOR_MODEL:-}"; BIN="${BOT_CURSOR_BIN:-cursor-agent}" ;;
  bravebot) MODEL="${BOT_BRAVEBOT_MODEL:-}"; BIN="${BOT_BRAVEBOT_BIN:-bravebot}" ;;
  *) echo "pr-feedback: unsupported agent '$BOT_AGENT'" >&2; OUTCOME=setup-failed; exit 0 ;;
esac
TIMEOUT=$(jq -r '.settings.timeoutSeconds' <<< "$TARGET")

echo "pr-feedback: running $BOT_AGENT in $WORKTREE (log: $LOG, timeout ${TIMEOUT}s)"
bot_launch_agent "$BOT_AGENT" "$BIN" "$MODEL" "$WORKTREE" "$LOG" "$PROMPT_FILE" "$FINAL_MSG" "$TIMEOUT" \
  || AGENT_RC=$?
echo "pr-feedback: agent exited $AGENT_RC"
exit 0
