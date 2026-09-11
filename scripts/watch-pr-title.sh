#!/bin/bash
# Retitle the terminal tab when this story's PR appears.
#
# A story that starts an iteration in "pending" has no PR number; the agent
# opens one partway through, and from that moment the PR number is what an
# operator wants to read off the tab. update-prd-status.py records it in the
# PRD, so that is what this polls.
#
# Run it in the background through exec-clean.sh: a child that inherits the
# run's slot-lock fd keeps the slot held after the run itself is gone.
#
# Usage: watch-pr-title.sh <prd-file> <story-id> <issue-number> <story-title> [interval-seconds]

set -u

_TITLE_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)"
# shellcheck source=./lib/terminal-title.sh
source "$_TITLE_LIB/terminal-title.sh"

if [ $# -lt 4 ]; then
  echo "Usage: watch-pr-title.sh <prd-file> <story-id> <issue-number> <story-title> [interval-seconds]" >&2
  exit 2
fi

PRD_FILE="$1"
STORY_ID="$2"
ISSUE="$3"
STORY_TITLE="$4"
INTERVAL="${5:-20}"

while sleep "$INTERVAL"; do
  # A run killed outright never gets to stop this watcher, and an orphan that
  # polls on would eventually retitle a tab that has moved to other work.
  if [ -n "${BOT_RUN_PID:-}" ] && ! kill -0 "$BOT_RUN_PID" 2>/dev/null; then
    exit 0
  fi
  PR_NUMBER=$(jq -r --arg id "$STORY_ID" \
    'first(.stories[] | select(.id == $id)) | .prNumber // empty' \
    "$PRD_FILE" 2>/dev/null) || PR_NUMBER=""
  [ -n "$PR_NUMBER" ] || continue
  bot_set_terminal_title "$(bot_story_title "$ISSUE" "$PR_NUMBER" "$STORY_ID" "$STORY_TITLE")"
  break
done
