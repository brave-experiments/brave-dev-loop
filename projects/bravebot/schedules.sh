#!/bin/bash
# bravebot's cron jobs. Sourced by scripts/sync-schedules.sh, which supplies
# the variables and the bot_cron_* helpers (scripts/lib/cron-jobs.sh) and wraps
# what each group prints in a crontab block for this project alone.
#
# Four runs a day: a long one overnight, a second long one at noon, and two
# shorter ones at 12:45 and in the evening. Every hour is off the hours brave-core's runs use, because
# both projects can be deployed on one machine and each one's run.sh drives its
# own agent session and its own checkout.
#
# The runs overlap by design — each takes its own run slot, and
# maxConcurrentRuns is well above four — so a cap is not there to keep the
# slot free for the next job. It is there so a wedged run cannot sit on a slot
# forever: iterations are capped at three hours each and nothing caps the run
# itself, so timeout-tree.sh ends each one a minute short of the next 01:00,
# killing the agent and its descendants, and run.sh's TERM handler hands the
# slot and the story claim back on the way out.
#
# The jobs are grouped, and each group is a function that prints its lines: a
# machine can install just the jobs that run.sh (make schedules-run), or all of
# them (make schedules). The review jobs moved to
# https://github.com/brave-experiments/reviewer.

schedule_run() {
  echo ""
  echo "# Main agent run — skip if no actionable stories"
  echo "# Gate check runs before git sync to avoid wasted fetches"
  echo "# Daily: one run of 30 iterations, overnight, killed at 23h59m"
  bot_cron_job "0 1 * * *" "./scripts/check-has-work.sh" \
    "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 86340 ./run.sh 30$(bot_cron_run_agent_args)" \
    "run-cron.log"

  echo ""
  echo "# Fourth agent run — skip if no actionable stories"
  echo "# Daily: one run of 30 iterations, noon, killed at 12h59m"
  bot_cron_job "0 12 * * *" "./scripts/check-has-work.sh" \
    "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 46740 ./run.sh 30$(bot_cron_run_agent_args)" \
    "run-cron.log"

  echo ""
  echo "# Second agent run — skip if no actionable stories"
  echo "# Daily: one run of 10 iterations, midday, killed at 12h14m"
  bot_cron_job "45 12 * * *" "./scripts/check-has-work.sh" \
    "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 44040 ./run.sh 10$(bot_cron_run_agent_args)" \
    "run-cron.log"

  echo ""
  echo "# Third agent run — skip if no actionable stories"
  echo "# Daily: one run of 10 iterations, evening, killed at 7h59m"
  bot_cron_job "0 17 * * *" "./scripts/check-has-work.sh" \
    "./scripts/sync-target-repo.sh && ./scripts/timeout-tree.sh 28740 ./run.sh 10$(bot_cron_run_agent_args)" \
    "run-cron.log"
  echo ""
  echo "# PR feedback — wake an agent only for a request for changes or red CI"
  echo "# The gate (pr-feedback.py gate) is plain gh reads plus the cache in"
  echo "# ~/.brave-dev-loop/pr-feedback: no tokens when there is nothing to do, and a"
  echo "# PR that keeps failing is capped by per-component, daily, weekly and"
  echo "# back-off limits. The job makes a worktree, runs one agent iteration,"
  echo "# re-requests review from the rejecting reviewer and removes the worktree."
  echo "# Runs as the bot (it pushes). One at a time. Minutes are off every other job's."
  bot_cron_job "2,7,12,17,22,27,32,37,42,47,52,57 * * * *" "python3 ./scripts/pr-feedback.py gate" \
    "./scripts/with-lock.sh pr-feedback --timeout 5400 -- ./scripts/pr-feedback.sh" \
    "pr-feedback-cron.log"
}
