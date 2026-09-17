#!/bin/bash
# brave-dev-loop's own cron jobs. Sourced by scripts/sync-schedules.sh, which
# supplies the variables and the bot_cron_* helpers (scripts/lib/cron-jobs.sh)
# and wraps whatever this file prints in the crontab block for this project
# alone.
#
# No sync-target-repo.sh in front of either command, unlike every other
# project's. That script moves a fork's default branch onto upstream's, and this
# project has no fork and no upstream remote, so it would exit having done
# nothing. The prologue bot_cron_job puts on every line -- fetch origin, checkout
# the branch, hard reset to it -- is what keeps the target repo current, because
# the target repo is this bot directory.
#
# The hours dodge the other two deployments a machine can be running: bravebot
# starts at 01:00 and 13:45, brave-core between 06:00 and 20:00, and each one
# drives its own agent session over its own checkout.

echo ""
echo "# Main agent run — skip if no actionable stories"
echo "# Gate check runs before git sync to avoid wasted fetches"
echo "# Daily: 6 iterations, overnight, killed at 4h30m — before brave-core's 07:45"
bot_cron_job "0 3 * * *" "./scripts/check-has-work.sh" \
  "./scripts/timeout-tree.sh 16200 ./run.sh 6" \
  "run-cron.log"

echo ""
echo "# Review PRs — skip if no recent open PRs"
echo "# Daily: once, late, after the day's human pull requests have landed"
bot_cron_job "30 21 * * *" "./scripts/check-new-prs.sh" \
  "$(bot_cron_agent review-prs '/review-prs 1d open auto reviewer-priority')" \
  "review-prs-cron.log"
