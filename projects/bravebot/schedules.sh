#!/bin/bash
# bravebot's cron jobs. Sourced by scripts/sync-schedules.sh, which supplies
# the variables and the bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps
# whatever this file prints in the crontab block for this project alone.
#
# Two runs a day: a long one overnight and a shorter one after lunch. Both
# hours are off every hour brave-core uses, because both projects can be
# deployed on one machine and each one's run.sh drives its own agent session
# and its own checkout.
#
# Iterations are capped at two hours each and nothing caps the run itself, so a
# wedged one would still hold the run slot when the overnight job fires and
# every night after that would exit on a busy slot. timeout-tree.sh ends each
# run a minute short of the next 01:00 — killing the agent and its descendants,
# and run.sh's TERM handler hands the slot and the story claim back on the way
# out.

echo ""
echo "# Main agent run — skip if no actionable stories"
echo "# Gate check runs before git sync to avoid wasted fetches"
echo "# Daily: one run of 20 iterations, overnight, killed at 23h59m"
bot_cron_job "0 1 * * *" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 86340 ./run.sh 20" \
  "run-cron.log"

echo ""
echo "# Second agent run — skip if no actionable stories"
echo "# Daily: one run of 10 iterations, afternoon, killed at 11h13m"
bot_cron_job "45 13 * * *" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 40380 ./run.sh 10" \
  "run-cron.log"

echo ""
echo "# Review requested from the bot — skip unless someone asked"
echo "# Gate check runs before git sync to avoid wasted fetches (96 runs/day, most exit early)"
echo "# This project schedules no unsolicited best-practices sweep: the bot reviews"
echo "# a bravebot PR when a human clicks Request review on it, and not otherwise."
echo "# Minutes are offset from brave-core's copy of this job, which can be"
echo "# deployed on the same machine."
bot_cron_job "8,23,38,53 * * * *" "./scripts/check-review-requests.sh" \
  "./scripts/sync-target-repo.sh && ./scripts/with-lock.sh review-prs -- ./scripts/review-requested.sh" \
  "review-requested-cron.log"
