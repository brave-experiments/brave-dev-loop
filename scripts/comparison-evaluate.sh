#!/bin/bash
# Step 3 of a comparison run: critique the base run against the comparison run,
# and file an issue for each gap that is the base tool's fault.
#
# The comparison run is treated as the gold standard. What matters is not who
# wrote the better patch but what the base *tool* did to its own run that the
# other tool did not do to its: a capability it lacked, a gate that stopped
# correct work, a loop it could not get out of, context it lost.
#
# Usage:
#   comparison-evaluate.sh --story-id US-004 --status pending --story-title "…" \
#     --base-session /tmp/base.json --comparison-session /tmp/comparison.json \
#     --prompt-file /tmp/p --log logs/evaluator-…log \
#     --agent claude [--agent-bin path] [--model name] [--timeout 3600] \
#     [--run-id …] [--slot 1] [--loop 1]
#
# The agent posts the issues itself, to project.issueRepository. It runs with the
# comparison guard in issue-create mode: it may open issues, and nothing else.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/agent-launch.sh"

STORY_ID=""
STORY_STATUS=""
STORY_TITLE=""
BASE_SESSION=""
COMPARISON_SESSION=""
PROMPT_FILE=""
LOG=""
AGENT=""
AGENT_BIN=""
MODEL=""
TIMEOUT=3600
RUN_ID=""
SLOT=""
LOOP=""

while [ $# -gt 0 ]; do
  case "$1" in
    --story-id) STORY_ID="$2"; shift 2 ;;
    --status) STORY_STATUS="$2"; shift 2 ;;
    --story-title) STORY_TITLE="$2"; shift 2 ;;
    --base-session) BASE_SESSION="$2"; shift 2 ;;
    --comparison-session) COMPARISON_SESSION="$2"; shift 2 ;;
    --prompt-file) PROMPT_FILE="$2"; shift 2 ;;
    --log) LOG="$2"; shift 2 ;;
    --agent) AGENT="$2"; shift 2 ;;
    --agent-bin) AGENT_BIN="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --slot) SLOT="$2"; shift 2 ;;
    --loop) LOOP="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "comparison-evaluate.sh: unknown option '$1'" >&2; exit 2 ;;
  esac
done

for required in STORY_ID BASE_SESSION COMPARISON_SESSION LOG AGENT; do
  if [ -z "${!required}" ]; then
    echo "comparison-evaluate.sh: --$(echo "$required" | tr 'A-Z_' 'a-z-') is required" >&2
    exit 2
  fi
done
for f in "$BASE_SESSION" "$COMPARISON_SESSION"; do
  if [ ! -f "$f" ]; then
    echo "comparison-evaluate.sh: '$f' does not exist" >&2
    exit 2
  fi
done

EVAL_PROMPT_FILE=$(mktemp "${TMPDIR:-/tmp}/evaluator-prompt.XXXXXX")
FINAL_MSG=$(mktemp "${TMPDIR:-/tmp}/evaluator-final.XXXXXX")
trap 'rm -f "$EVAL_PROMPT_FILE" "$FINAL_MSG"' EXIT INT TERM HUP

{
  cat <<EOF
You are evaluating one autonomous coding run against another. Read
$BOT_DIR/docs/comparison-evaluation.md and follow it exactly — it defines what counts as a
finding, what does not, and the shape of the issue you file.

Story $STORY_ID (status when the runs started: $STORY_STATUS)
$( [ -n "$STORY_TITLE" ] && echo "Title: $STORY_TITLE" )
Issue repository for your findings: $BOT_ISSUE_REPO
Run: ${RUN_ID:-unknown}, slot ${SLOT:-1}, loop ${LOOP:-1}

BASE RUN — the run under critique:
$(cat "$BASE_SESSION")

COMPARISON RUN — the gold standard, working the same story from scratch:
$(cat "$COMPARISON_SESSION")
EOF

  if [ -n "$PROMPT_FILE" ] && [ -f "$PROMPT_FILE" ]; then
    cat <<EOF

Both runs were given the prompt in this file, so read it to see what the task actually was:
  $PROMPT_FILE
EOF
  fi

  cat <<EOF

You may open issues in $BOT_ISSUE_REPO and nothing else: the \`gh\` on your PATH refuses
pull request writes, comments, and \`git push\`, and the PRD and progress log are closed to
you. Do not touch the story's own pull request or branch.

Finish by printing, in a few lines, how many issues you filed and their URLs — or that the
base run had no gap worth filing, and why.
EOF
} > "$EVAL_PROMPT_FILE"

export BOT_COMPARISON_RUN=1
export BOT_COMPARISON_GUARD_ALLOW=issue-create
export PATH="$SCRIPT_DIR/comparison-guard:$PATH"

AGENT_RC=0
bot_launch_agent "$AGENT" "${AGENT_BIN:-$AGENT}" "$MODEL" \
  "$BOT_DIR" "$LOG" "$EVAL_PROMPT_FILE" "$FINAL_MSG" "$TIMEOUT" || AGENT_RC=$?

echo ""
if [ "$AGENT_RC" -ne 0 ]; then
  echo "  Evaluator exited $AGENT_RC — see $LOG" >&2
fi
echo "  Evaluator verdict:"
sed 's/^/    /' "$FINAL_MSG" | tail -20 || true
exit "$AGENT_RC"
