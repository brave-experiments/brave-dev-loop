#!/bin/bash
# Helpers for writing a project's cron jobs.
#
# The jobs themselves live in projects/<profile>/schedules.sh, one file per
# project, and every line in them is built from these two functions so that
# every job gets the same prologue: the bot directory, the bot identity, its
# gate, and a hard reset of the bot repo to the branch the jobs run from.
#
# sync-schedules.sh sources this file and sets the variables the functions
# read before it sources the profile's schedules.sh:
#
#   PROJECT_ROOT      absolute path of the bot directory
#   BOT_REPO_BRANCH   branch the jobs check out and reset to
#   LOG_DIR           where job output is appended
#   CLAUDE_BIN        the agent binary an agent job invokes
#   CLAUDE_TOOLS      --allowedTools value for an agent job

# One crontab line.
#
#   bot_cron_job "<schedule>" "<gate>" "<command>" "<log file name>"
#
# The gate is a script that exits non-zero when there is nothing to do; it runs
# before the git sync so a job with no work costs no fetches. Pass "" for a job
# that always runs.
bot_cron_job() {
  local schedule="$1" gate="$2" command="$3" log="$4"
  local line="$schedule cd $PROJECT_ROOT && source .envrc"
  [ -n "$gate" ] && line="$line && $gate"
  line="$line && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH"
  printf '%s && %s >> %s/%s 2>&1\n' "$line" "$command" "$LOG_DIR" "$log"
}

# The command for a job that starts an agent session, held under a named lock
# so two schedules never run the same job twice over one bot directory.
#
#   bot_cron_agent <lock name> '<prompt>'
bot_cron_agent() {
  printf "./scripts/with-lock.sh %s -- %s -p '%s' --allowedTools '%s'" \
    "$1" "$CLAUDE_BIN" "$2" "$CLAUDE_TOOLS"
}
