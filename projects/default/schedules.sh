#!/bin/bash
# The cron jobs a project gets when its profile defines none of its own.
# Sourced by scripts/sync-schedules.sh, which supplies the variables and the
# bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps whatever this file
# prints in the crontab block for this project alone.
#
# Only jobs that make sense for any target repo belong here — the ones every
# deployment ran before schedules were per-project, minus the monthly
# best-practices refresh, which reads Chromium's documentation and so belongs
# to brave-core. A project that wants different jobs, or the same jobs at
# different hours, copies this file to projects/<name>/schedules.sh.

echo ""
echo "# Refresh the PRD cache / backlog before the first run.sh of the day."
echo "# Runs the deterministic sync scripts directly: no agent session, no tokens."
echo "# In prdMode \"auto\" run.sh refreshes the cache itself too, so this is only a"
echo "# head start, not a requirement."
echo "# Gate check runs before git sync to avoid wasted fetches"
echo "# Weekdays: 1x/day"
bot_cron_job "45 7 * * 1-5" "./scripts/check-has-prd.sh" \
  "./scripts/with-lock.sh add-backlog -- ./scripts/sync-prd.sh" "add-backlog-cron.log"
echo "# Weekends: once/day (before run.sh)"
bot_cron_job "45 11 * * 0,6" "./scripts/check-has-prd.sh" \
  "./scripts/with-lock.sh add-backlog -- ./scripts/sync-prd.sh" "add-backlog-cron.log"

echo ""
echo "# Main agent run — skip if no actionable stories"
echo "# Gate check runs before git sync to avoid wasted fetches"
echo "# Weekdays: 2x/day"
bot_cron_job "10 8 * * 1-5" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./run.sh 3" "run-cron.log"
echo "# Weekends: once/day at 14:10"
bot_cron_job "10 14 * * 0,6" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./run.sh 3" "run-cron.log"

echo ""
echo "# Review PRs — skip if no recent open PRs"
echo "# Gate check runs before git sync to avoid wasted fetches"
echo "# Weekdays: 3x/day"
bot_cron_job "0 13,20 * * 1-5" "./scripts/check-new-prs.sh" \
  "./scripts/sync-target-repo.sh && $(bot_cron_agent review-prs '/review-prs 1d open auto reviewer-priority')" \
  "review-prs-cron.log"
echo "# Weekends: once/day at noon"
bot_cron_job "0 12 * * 0,6" "./scripts/check-new-prs.sh" \
  "./scripts/sync-target-repo.sh && $(bot_cron_agent review-prs '/review-prs 1d open auto reviewer-priority')" \
  "review-prs-cron.log"

echo ""
echo "# Learnable pattern search — skip if no recent merged PRs"
echo "# Gate check runs before git sync to avoid wasted fetches"
bot_cron_job "0 6 * * *" "./scripts/check-bot-prs.sh" \
  "./scripts/sync-target-repo.sh && $(bot_cron_agent learnable-pattern-search '/learnable-pattern-search 2d')" \
  "learnable-pattern-search-cron.log"

echo ""
echo "# Check Signal messages (every 5 min, offset to avoid git lock contention with other jobs)"
echo "# Gate check runs before git sync to avoid wasted fetches (288 runs/day, most exit early)"
bot_cron_job "1,6,11,16,21,26,31,36,41,46,51,56 * * * *" "./scripts/check-signal-messages.sh" \
  "$(bot_cron_agent check-signal '/check-signal')" "check-signal-cron.log"

if [ "$SYNC_REPO_ENABLED" = "true" ] && [ -n "$SYNC_REPO_PATH" ]; then
  echo ""
  echo "# Sync repo origin/$BOT_DEFAULT_BRANCH from upstream/$BOT_DEFAULT_BRANCH (daily at 00:01)"
  echo "1 0 * * * cd $SYNC_REPO_PATH && git fetch upstream && git push origin upstream/$BOT_DEFAULT_BRANCH:$BOT_DEFAULT_BRANCH && git fetch origin >> $LOG_DIR/sync-repo-cron.log 2>&1"
fi
