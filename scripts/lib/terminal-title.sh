#!/bin/bash
# The terminal tab's title, for an operator watching several runs at once.
#
# Left alone, Claude titles the tab with a summary of its own conversation
# ("US-036 trust map vouch prompt bytes"). That names the story by an id only
# this bot uses, and buries the numbers an operator actually switches tabs to
# find: the GitHub issue, and the PR once one exists. So a run titles its own
# tab and exports CLAUDE_CODE_DISABLE_TERMINAL_TITLE, which stops the agent
# overwriting it.

# Where the title is written. The terminal, not stdout: in --print mode stdout
# is a pipe into the iteration log, where an escape sequence is only noise.
: "${BOT_TITLE_TTY:=/dev/tty}"

# "#29 PR #250 trust map: the vouch prompt branches on the file's own bytes".
# Numbers lead because a tab truncates from the right, so the story's own words
# are what gets cut; the story id stands in only while there is no number yet.
bot_story_title() {
  local issue="$1" pr="$2" story_id="$3" story_title="$4" prefix=""
  [ -n "$issue" ] && prefix="#$issue"
  [ -n "$pr" ] && prefix="${prefix:+$prefix }PR #$pr"
  [ -n "$prefix" ] || prefix="$story_id"
  # A story title is a GitHub issue title — text this bot did not write. An ESC
  # or BEL inside it would end the escape sequence early and leave the rest to
  # be read as terminal commands.
  printf '%s %s' "$prefix" "$story_title" | tr -d '\000-\037\177'
}

bot_set_terminal_title() {
  [ -w "$BOT_TITLE_TTY" ] || return 0
  # A run started by a scheduler has no controlling terminal, so the open fails
  # rather than the test above: nothing to report, nothing to retry.
  printf '\033]0;%s\007' "$1" >>"$BOT_TITLE_TTY" 2>/dev/null || true
}
