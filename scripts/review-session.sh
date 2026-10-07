#!/bin/bash
# One scheduled /review-prs run, with GitHub kept out of the model's reach.
#
#   ./scripts/review-session.sh [--model <model>] -- <prepare-review.py args>
#   ./scripts/review-session.sh --model sonnet -- 1d open --auto --reviewer-priority
#   ./scripts/review-session.sh -- '#1668' open --auto
#
# The skill's six steps split three ways. The two that talk to GitHub run here,
# as whoever this script runs as (the reviewer, under as-reviewer.sh):
#
#   1. prepare-review.py fetches the PRs, their diffs and comments, and writes
#      the prompts into a work directory.
#   6. collect-results.py posts what the validators kept, resolves threads,
#      and approves or requests changes.
#
# Steps 2-5 -- reading the manifest, the detect and validate subagents, and
# select-candidates.py between them -- run in a claude session that has no
# GitHub token at all and runs under the rules review-session-settings.py
# writes. That session is the part that reads untrusted text: a PR's diff, its
# description, and its whole source tree. With the reviewer able to write to the
# repository, a session that held the token and an unrestricted Bash could be
# talked into pushing, merging or posting by a comment in the code it reviews.
# Here it can read its work directory, write into it, and launch subagents, and
# the only thing that leaves it is the validators' results file, which the
# collector posts through its own templates.
#
# Exits non-zero when any of the three steps failed. The collector runs even
# after a failed session: a PR whose validator wrote nothing is left out of what
# it posts, and it is what removes the run's worktrees.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

SKILL_DIR="${REVIEW_PRS_SKILL_DIR:-$BOT_DIR/.claude/skills/review-prs}"
MODEL="$BOT_REVIEW_MODEL"
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "Usage: review-session.sh [--model <model>] -- <prepare-review.py args>" >&2; exit 2 ;;
  esac
done

POINTER=$(python3 "$SKILL_DIR/prepare-review.py" "$@") || {
  echo "prepare-review.py failed; nothing to review." >&2
  exit 1
}
WORK_DIR=$(jq -r '.work_dir // empty' <<< "$POINTER" 2>/dev/null)
if [ -z "$WORK_DIR" ] || [ ! -f "$WORK_DIR/manifest.json" ]; then
  echo "prepare-review.py named no work directory: $POINTER" >&2
  exit 1
fi
AUTO=()
[ "$(jq -r '.auto_mode' "$WORK_DIR/manifest.json")" = "true" ] && AUTO=(--auto)

# An empty gh config directory as well as no token: gh falls back to its default
# config directory, which holds the bot's login.
NO_GH=$(mktemp -d)
trap 'rm -rf "$NO_GH"' EXIT

SETTINGS=$(python3 "$SCRIPT_DIR/review-session-settings.py" --work-dir "$WORK_DIR" \
  ${BOT_GH_CONFIG_DIR:+--deny-read "$BOT_GH_CONFIG_DIR"} \
  ${BOT_REVIEWER_GH_CONFIG_DIR:+--deny-read "$BOT_REVIEWER_GH_CONFIG_DIR"} \
  ${GH_CONFIG_DIR:+--deny-read "$GH_CONFIG_DIR"}) || exit 1

RC=0
(
  cd "$BOT_DIR" || exit 1
  exec env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
    GH_CONFIG_DIR="$NO_GH" \
    "$BOT_CLAUDE_BIN" -p "/review-prs --work-dir $WORK_DIR" --model "$MODEL" \
    --permission-mode dontAsk --settings "$SETTINGS" --add-dir "$WORK_DIR" \
    --strict-mcp-config </dev/null
) || RC=$?
[ "$RC" -eq 0 ] || echo "The review session exited $RC; collecting what it wrote." >&2

python3 "$SKILL_DIR/collect-results.py" --work-dir "$WORK_DIR" "${AUTO[@]}" || RC=$?
exit "$RC"
