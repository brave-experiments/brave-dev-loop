#!/bin/bash
# Step 2 of a comparison run: do the story again, from scratch, in a worktree of
# its own, and hand the session id back to run.sh.
#
# The point is a second opinion on the same task, produced under the same
# conditions, so the evaluator in step 3 has something to hold the base run
# against. This run therefore does the whole job — research, implement, test,
# commit — and stops dead before anything public. The prompt says so and
# scripts/comparison-guard/ makes it true.
#
# Usage:
#   comparison-run.sh --story-id US-004 --status pending \
#     --branch comparison-us-004-1789 --prompt-file /tmp/p --log logs/comparison-…log \
#     --session-out /tmp/session.json \
#     --agent claude [--agent-bin path] [--model name] \
#     [--base-agent bravebot] [--base-session-id 1788…] [--timeout 7200]
#
# Prints progress for the operator. Exits non-zero when the comparison could not
# be produced; run.sh treats that as a skipped comparison, never as a failed
# iteration.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/agent-launch.sh"

STORY_ID=""
STORY_STATUS=""
BRANCH=""
PROMPT_FILE=""
LOG=""
SESSION_OUT=""
AGENT=""
AGENT_BIN=""
MODEL=""
BASE_AGENT=""
BASE_SESSION_ID=""
TIMEOUT=7200

while [ $# -gt 0 ]; do
  case "$1" in
    --story-id) STORY_ID="$2"; shift 2 ;;
    --status) STORY_STATUS="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --prompt-file) PROMPT_FILE="$2"; shift 2 ;;
    --log) LOG="$2"; shift 2 ;;
    --session-out) SESSION_OUT="$2"; shift 2 ;;
    --agent) AGENT="$2"; shift 2 ;;
    --agent-bin) AGENT_BIN="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --base-agent) BASE_AGENT="$2"; shift 2 ;;
    --base-session-id) BASE_SESSION_ID="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "comparison-run.sh: unknown option '$1'" >&2; exit 2 ;;
  esac
done

for required in STORY_ID BRANCH PROMPT_FILE LOG SESSION_OUT AGENT; do
  if [ -z "${!required}" ]; then
    echo "comparison-run.sh: --$(echo "$required" | tr 'A-Z_' 'a-z-') is required" >&2
    exit 2
  fi
done
if [ ! -f "$PROMPT_FILE" ]; then
  echo "comparison-run.sh: prompt file '$PROMPT_FILE' does not exist" >&2
  exit 2
fi

MAIN="$BOT_TARGET_REPO_DIR"
DEFAULT_BRANCH="${BOT_DEFAULT_BRANCH:-main}"
# The branch is the operator's to name, so it may contain characters a directory
# name should not. The worktree is a sibling of the checkout, as this project's
# story worktrees are.
BRANCH_SLUG=$(printf '%s' "$BRANCH" | tr -c 'a-zA-Z0-9._-' '-')
WORK="$MAIN-$BRANCH_SLUG"
LOCK="$SCRIPT_DIR/git-repo-lock.sh"

if [ -e "$WORK" ]; then
  echo "comparison-run.sh: '$WORK' already exists — refusing to reuse it." >&2
  echo "  A comparison run works from scratch. Move it aside or pass another --branch." >&2
  exit 1
fi
if git -C "$MAIN" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  echo "comparison-run.sh: branch '$BRANCH' already exists in $MAIN — refusing to reuse it." >&2
  exit 1
fi

# The comparison starts from the same tree the base run branches from: the
# upstream default branch, not the fork's copy of it. Every command against the
# shared .git goes through the repo lock — another run may be adding a worktree
# in the same repository right now.
BASE_REF=""
if git -C "$MAIN" remote get-url upstream >/dev/null 2>&1; then
  "$LOCK" "$MAIN" -- git -C "$MAIN" fetch upstream >/dev/null 2>&1 || true
  BASE_REF="upstream/$DEFAULT_BRANCH"
elif git -C "$MAIN" remote get-url origin >/dev/null 2>&1; then
  "$LOCK" "$MAIN" -- git -C "$MAIN" fetch origin >/dev/null 2>&1 || true
  BASE_REF="origin/$DEFAULT_BRANCH"
fi
if [ -z "$BASE_REF" ] || ! git -C "$MAIN" rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
  BASE_REF="$DEFAULT_BRANCH"
fi
if ! git -C "$MAIN" rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
  echo "comparison-run.sh: cannot resolve a base ref for the comparison (tried $BASE_REF)." >&2
  exit 1
fi

echo "  Base ref:  $BASE_REF ($(git -C "$MAIN" rev-parse --short "$BASE_REF"))"
echo "  Worktree:  $WORK"
if ! "$LOCK" "$MAIN" -- git -C "$MAIN" worktree add -b "$BRANCH" "$WORK" "$BASE_REF"; then
  echo "comparison-run.sh: could not create the comparison worktree at $WORK." >&2
  exit 1
fi

COMPARISON_PROMPT_FILE=$(mktemp "${TMPDIR:-/tmp}/comparison-prompt.XXXXXX")
trap 'rm -f "$COMPARISON_PROMPT_FILE"' EXIT INT TERM HUP

{
  cat <<EOF
This is a COMPARISON RUN of story $STORY_ID. Another tool has already worked this story;
you are doing the same work independently so the two attempts can be compared. Do the job
properly and completely — the comparison is only worth something if this is your best work.

Work in this worktree, which is already created and checked out for you:

  $WORK   (branch $BRANCH, based on $BASE_REF)

Everywhere the instructions below name the target repository, or a path inside it, they
mean that worktree. Never touch the main checkout, and never touch the branch or worktree
the base run used.

--- The story's own instructions follow, unchanged. ---

EOF
  cat "$PROMPT_FILE"
  cat <<EOF

--- Where this run stops. ---

Do the whole task the workflow doc describes: research, reproduce, implement, run every
validation and test it asks for, and commit in the worktree. **Stop there.** This run must
not:

- push a branch, or open, update, or comment on a pull request
- create or comment on an issue, or post anywhere else public
- update the PRD (scripts/update-prd-status.py) or data/progress.txt
- touch the story's real branch, worktree, or pull request

The \`gh\` and \`git\` on your PATH are wrappers that refuse those commands. A refusal from
them is this rule working, not a broken tool — do not look for a way around it, and do not
treat it as a task failure.

Finish by printing a short summary for the evaluator that reads this session: what the
problem was, what you changed and where, which validations you ran and their results, and
anything that fought you or that you could not do.
EOF
} > "$COMPARISON_PROMPT_FILE"

# Everything below runs with the guard in front of the real tools.
export BOT_COMPARISON_RUN=1
export PATH="$SCRIPT_DIR/comparison-guard:$PATH"

FINAL_MSG=$(mktemp "${TMPDIR:-/tmp}/comparison-final.XXXXXX")
trap 'rm -f "$COMPARISON_PROMPT_FILE" "$FINAL_MSG"' EXIT INT TERM HUP

AGENT_RC=0
bot_launch_agent "$AGENT" "${AGENT_BIN:-$AGENT}" "$MODEL" \
  "$BOT_DIR" "$LOG" "$COMPARISON_PROMPT_FILE" "$FINAL_MSG" "$TIMEOUT" || AGENT_RC=$?

if [ "$AGENT_RC" -ne 0 ]; then
  echo "  Comparison agent exited $AGENT_RC — evaluating what it did get done." >&2
fi

SESSION_JSON=$(bot_find_launched_session \
  "$AGENT" "$BOT_DIR" "$BOT_LAUNCH_STARTED_AT" "$BOT_LAUNCH_SESSION_ID" "$BASE_SESSION_ID")

if [ -z "$SESSION_JSON" ]; then
  echo "comparison-run.sh: could not identify the $AGENT session for this run." >&2
  echo "  The log at $LOG is still the record of what it did." >&2
  SESSION_JSON='{}'
fi

# What run.sh hands to step 3: the session, plus where the work landed.
echo "$SESSION_JSON" | jq \
  --arg branch "$BRANCH" --arg worktree "$WORK" --arg log "$LOG" \
  --arg agent "$AGENT" --arg baseRef "$BASE_REF" --argjson exitCode "$AGENT_RC" \
  '. + {branch: $branch, worktree: $worktree, log: $log, agent: $agent, baseRef: $baseRef, exitCode: $exitCode}' \
  > "$SESSION_OUT"

COMMITS=$(git -C "$WORK" rev-list --count "$BASE_REF..HEAD" 2>/dev/null || echo "0")
echo ""
echo "  Comparison run finished: $COMMITS commit(s) on $BRANCH in $WORK"
echo "  Session:   $(jq -r '.sessionId // "unknown"' "$SESSION_OUT")"
echo ""
echo "  Its summary:"
sed 's/^/    /' "$FINAL_MSG" | tail -20 || true
