#!/bin/bash
# brave-core's cron jobs. Sourced by scripts/sync-schedules.sh, which supplies
# the variables and the bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps
# whatever this file prints in the crontab block for this project alone.
#
# Every job is gated: the gate script exits non-zero when there is nothing to
# do, before any fetch, so an idle day costs nothing. add-backlog-to-prd runs
# 15 min before the first run.sh of the day. Weekday schedules (1-5 = Mon-Fri)
# run more often than weekend ones (0,6 = Sat-Sun).

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
echo "# Three slots, the same count the review-request poll below asks for: a"
echo "# sweep asking for one would take slot 1 only, and exit doing nothing"
echo "# every time a poll happened to be holding it."
echo "# Weekdays: 3x/day"
bot_cron_job "0 13,20 * * 1-5" "./scripts/check-new-prs.sh" \
  "./scripts/sync-target-repo.sh && $(bot_cron_agent review-prs '/review-prs 1d open auto reviewer-priority' 3)" \
  "review-prs-cron.log"
echo "# Weekends: once/day at noon"
bot_cron_job "0 12 * * 0,6" "./scripts/check-new-prs.sh" \
  "./scripts/sync-target-repo.sh && $(bot_cron_agent review-prs '/review-prs 1d open auto reviewer-priority' 3)" \
  "review-prs-cron.log"

echo ""
echo "# Review requested from the bot — skip unless someone asked"
echo "# Gate check runs before git sync to avoid wasted fetches (288 runs/day, most exit early)"
echo "# Every 5 min, on minutes no other job here uses. A review runs for far"
echo "# longer than the gap between polls, so runs overlap rather than waiting:"
echo "# three review-prs slots, shared with the sweep above, and a lock per PR"
echo "# inside the job so two runs never review the same one."
bot_cron_job "4,9,14,19,24,29,34,39,44,49,54,59 * * * *" "./scripts/check-review-requests.sh" \
  "./scripts/sync-target-repo.sh && ./scripts/with-lock.sh review-prs --slots 3 --timeout 14400 -- ./scripts/review-requested.sh" \
  "review-requested-cron.log"

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

echo ""
echo "# Update best practices from upstream Chromium docs (monthly, 1st of each month)"
bot_cron_job "15 4 1 * *" "" \
  "./scripts/sync-target-repo.sh && $(bot_cron_agent update-best-practices '/update-best-practices')" \
  "update-best-practices-cron.log"

if [ "$SYNC_REPO_ENABLED" = "true" ] && [ -n "$SYNC_REPO_PATH" ]; then
  echo ""
  echo "# Sync repo origin/$BOT_DEFAULT_BRANCH from upstream/$BOT_DEFAULT_BRANCH (daily at 00:01)"
  echo "1 0 * * * cd $SYNC_REPO_PATH && git fetch upstream && git push origin upstream/$BOT_DEFAULT_BRANCH:$BOT_DEFAULT_BRANCH && git fetch origin >> $LOG_DIR/sync-repo-cron.log 2>&1"
fi
