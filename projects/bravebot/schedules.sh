#!/bin/bash
# bravebot's cron jobs. Sourced by scripts/sync-schedules.sh, which supplies
# the variables and the bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps
# whatever this file prints in the crontab block for this project alone.
#
# One job for now: a single long run, daily. The hour is off every hour
# brave-core uses, because both projects can be deployed on one machine and
# each one's run.sh drives its own agent session and its own checkout.

echo ""
echo "# Main agent run — skip if no actionable stories"
echo "# Gate check runs before git sync to avoid wasted fetches"
echo "# Daily: one run of 20 iterations, overnight"
bot_cron_job "0 1 * * *" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./run.sh 20" "run-cron.log"
