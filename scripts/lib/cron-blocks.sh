#!/bin/bash
# The crontab block this project's scheduled jobs live in.
#
# The marker carries the project name so several deployments can share one
# crontab. It also carries this repo's name, and that name changed once:
# a block written before the rename does not match the current marker, so an
# exact-match strip leaves the old jobs installed and adds a second copy of
# every one of them. Strip every spelling this repo has ever written, not just
# the current one.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/cron-blocks.sh"
#   marker=$(bot_cron_marker "$BOT_PROJECT_NAME")
#   cleaned=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" <<< "$existing")

# Current spelling first; every earlier one after it.
BOT_CRON_MARKER_NAMES=(brave-dev-loop brave-dev-bot)

# The marker to write. Parentheses rather than brackets: the marker has to
# survive pattern matching without escaping.
bot_cron_marker() {
  printf '%s (%s)\n' "${BOT_CRON_MARKER_NAMES[0]}" "$1"
}

# Remove this project's block, in any spelling, from the crontab on stdin.
# Fixed-string matching, so a project name with regex characters in it is not
# a special case.
bot_strip_cron_blocks() {
  local project="$1" name marker text
  text=$(cat)
  for name in "${BOT_CRON_MARKER_NAMES[@]}"; do
    marker="$name ($project)"
    text=$(awk -v start="# === $marker scheduled jobs ===" \
               -v end="# === end $marker ===" \
               '$0==start{skip=1} $0==end{skip=0;next} !skip' <<< "$text")
  done
  printf '%s\n' "$text"
}
