#!/bin/bash
# bravebot's cron jobs. Sourced by scripts/sync-schedules.sh, which supplies
# the variables and the bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps
# whatever this file prints in the crontab block for this project alone.
#
# Three runs a day: a long one overnight, and two shorter ones at midday and
# in the evening. Every hour is off the hours brave-core's runs use, because
# both projects can be deployed on one machine and each one's run.sh drives its
# own agent session and its own checkout.
#
# The runs overlap by design — each takes its own run slot, and
# maxConcurrentRuns is well above three — so a cap is not there to keep the
# slot free for the next job. It is there so a wedged run cannot sit on a slot
# forever: iterations are capped at two hours each and nothing caps the run
# itself, so timeout-tree.sh ends each one a minute short of the next 01:00,
# killing the agent and its descendants, and run.sh's TERM handler hands the
# slot and the story claim back on the way out.

echo ""
echo "# Main agent run — skip if no actionable stories"
echo "# Gate check runs before git sync to avoid wasted fetches"
echo "# Daily: one run of 30 iterations, overnight, killed at 23h59m"
bot_cron_job "0 1 * * *" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 86340 ./run.sh 30" \
  "run-cron.log"

echo ""
echo "# Second agent run — skip if no actionable stories"
echo "# Daily: one run of 10 iterations, midday, killed at 12h14m"
bot_cron_job "45 12 * * *" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 44040 ./run.sh 10" \
  "run-cron.log"

echo ""
echo "# Third agent run — skip if no actionable stories"
echo "# Daily: one run of 10 iterations, evening, killed at 7h59m"
bot_cron_job "0 17 * * *" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 28740 ./run.sh 10" \
  "run-cron.log"

echo ""
echo "# Review requested from the bot — skip unless someone asked"
echo "# Gate check runs before git sync to avoid wasted fetches (288 runs/day, most exit early)"
echo "# This project schedules no unsolicited best-practices sweep: the bot reviews"
echo "# a bravebot PR when a human clicks Request review on it, and not otherwise."
echo "# Every 5 min, and runs overlap — a review is much longer than the gap —"
echo "# bounded by three review-prs slots, with a lock per PR inside the job."
echo "# Minutes are offset from brave-core's copy of this job, which can be"
echo "# deployed on the same machine."
bot_cron_job "3,8,13,18,23,28,33,38,43,48,53,58 * * * *" "./scripts/check-review-requests.sh" \
  "./scripts/sync-target-repo.sh && ./scripts/with-lock.sh review-prs --slots 3 --timeout 14400 -- ./scripts/review-requested.sh" \
  "review-requested-cron.log"
