#!/bin/bash
# The crontab blocks this project's scheduled jobs live in.
#
# The marker carries the project name so several deployments can share one
# crontab. It also carries this repo's name, and that name changed once:
# a block written before the rename does not match the current marker, so an
# exact-match strip leaves the old jobs installed and adds a second copy of
# every one of them. Strip every spelling this repo has ever written, not just
# the current one.
#
# A project's jobs come in groups, and each group is a block of its own so one
# can be installed or removed without touching the others:
#
#   run          run.sh, and the PRD sync that feeds it
#   review       the review-prs sweeps and the poll for requested reviews
#   maintenance  everything else: pattern search, Signal, the repo sync
#
# A block written before groups existed holds all of them under the bare
# project marker. Installing or removing every group replaces it; one group
# cannot be split out of it, so that is refused instead.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/cron-blocks.sh"
#   marker=$(bot_cron_marker "$BOT_PROJECT_NAME" review)
#   cleaned=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" <<< "$existing")

# Current spelling first; every earlier one after it.
BOT_CRON_MARKER_NAMES=(brave-dev-loop brave-dev-bot)

BOT_CRON_GROUPS=(run review maintenance)

# The marker to write. Parentheses rather than brackets: the marker has to
# survive pattern matching without escaping. The group, when there is one,
# follows the project name after a colon.
bot_cron_marker() {
  printf '%s (%s%s)\n' "${BOT_CRON_MARKER_NAMES[0]}" "$1" "${2:+:$2}"
}

# Remove blocks from the crontab on stdin, in any spelling of the repo's name.
# With a group, only that group's block; without one, the pre-group block and
# every group's. Fixed-string matching, so a project name with regex characters
# in it is not a special case.
bot_strip_cron_blocks() {
  local project="$1" group="${2:-}" name marker text g
  local -a labels
  text=$(cat)
  if [ -n "$group" ]; then
    labels=("$project:$group")
  else
    labels=("$project")
    for g in "${BOT_CRON_GROUPS[@]}"; do labels+=("$project:$g"); done
  fi
  for name in "${BOT_CRON_MARKER_NAMES[@]}"; do
    for g in "${labels[@]}"; do
      marker="$name ($g)"
      text=$(awk -v start="# === $marker scheduled jobs ===" \
                 -v end="# === end $marker ===" \
                 '$0==start{skip=1} $0==end{skip=0;next} !skip' <<< "$text")
    done
  done
  printf '%s\n' "$text"
}

# Succeeds when the crontab on stdin holds a block written before groups
# existed: one marked with the bare project name.
bot_has_ungrouped_cron_block() {
  local project="$1" name text
  text=$(cat)
  for name in "${BOT_CRON_MARKER_NAMES[@]}"; do
    grep -qxF "# === $name ($project) scheduled jobs ===" <<< "$text" && return 0
  done
  return 1
}
