#!/bin/bash
# Idempotent cron job setup for brave-dev-loop
# Run this script to install/update all cron jobs.
# All schedule changes should be made here and committed to source control.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/cron-blocks.sh"

CLAUDE_BIN="$BOT_CLAUDE_BIN"
CLAUDE_TOOLS="Bash,Read,Glob,Grep,Write,Edit,Task,WebFetch"
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# Derive the PATH from CLAUDE_BIN's directory
CLAUDE_BIN_DIR=$(dirname "$CLAUDE_BIN")

# Resolve sync repo path from config (optional — only for projects that sync from upstream)
SYNC_REPO_ENABLED=$(bot_config_bool '.schedules.syncRepo')
SYNC_REPO_PATH=$(bot_config '.schedules.syncRepoPath')

# Bot repo's own default branch (for cron git checkout/reset)
BOT_REPO_BRANCH=$(bot_config '.project.botRepoBranch')
BOT_REPO_BRANCH="${BOT_REPO_BRANCH:-master}"

# The marker carries the project name so several deployments can share one
# crontab. cron-blocks.sh owns its spelling, and the older spellings it has to
# strip alongside it.
CRON_MARKER=$(bot_cron_marker "$BOT_PROJECT_NAME")

# Build the crontab content
# Note: add-backlog-to-prd runs 15 min before each run.sh invocation
# Weekday (1-5 = Mon-Fri) schedules run more frequently than weekend (0,6 = Sat-Sun)
CRON_JOBS=$(cat <<EOF
# === $CRON_MARKER scheduled jobs ===
# Managed by sync-schedules.sh - do not edit manually
SHELL=/bin/bash
PATH=$CLAUDE_BIN_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin

# Refresh the PRD cache / backlog before the first run.sh of the day.
# Runs the deterministic sync scripts directly: no agent session, no tokens.
# In prdMode "auto" run.sh refreshes the cache itself too, so this is only a
# head start, not a requirement.
# Gate check runs before git sync to avoid wasted fetches
# Weekdays: 1x/day
45 7 * * 1-5 cd $PROJECT_ROOT && source .envrc && ./scripts/check-has-prd.sh && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/with-lock.sh add-backlog -- ./scripts/sync-prd.sh >> $LOG_DIR/add-backlog-cron.log 2>&1
# Weekends: once/day (before run.sh)
45 11 * * 0,6 cd $PROJECT_ROOT && source .envrc && ./scripts/check-has-prd.sh && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/with-lock.sh add-backlog -- ./scripts/sync-prd.sh >> $LOG_DIR/add-backlog-cron.log 2>&1

# Main agent run — skip if no actionable stories
# Gate check runs before git sync to avoid wasted fetches
# Weekdays: 2x/day
10 8 * * 1-5 cd $PROJECT_ROOT && source .envrc && ./scripts/check-has-work.sh && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/sync-target-repo.sh && ./run.sh 3 >> $LOG_DIR/run-cron.log 2>&1
# Weekends: once/day at 14:10
10 14 * * 0,6 cd $PROJECT_ROOT && source .envrc && ./scripts/check-has-work.sh && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/sync-target-repo.sh && ./run.sh 3 >> $LOG_DIR/run-cron.log 2>&1

# Review PRs — skip if no recent open PRs
# Gate check runs before git sync to avoid wasted fetches
# Weekdays: 3x/day
0 13,20 * * 1-5 cd $PROJECT_ROOT && source .envrc && ./scripts/check-new-prs.sh && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/sync-target-repo.sh && ./scripts/with-lock.sh review-prs -- $CLAUDE_BIN -p '/review-prs 1d open auto reviewer-priority' --allowedTools '$CLAUDE_TOOLS' >> $LOG_DIR/review-prs-cron.log 2>&1
# Weekends: once/day at noon
0 12 * * 0,6 cd $PROJECT_ROOT && source .envrc && ./scripts/check-new-prs.sh && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/sync-target-repo.sh && ./scripts/with-lock.sh review-prs -- $CLAUDE_BIN -p '/review-prs 1d open auto reviewer-priority' --allowedTools '$CLAUDE_TOOLS' >> $LOG_DIR/review-prs-cron.log 2>&1

# Learnable pattern search — skip if no recent merged PRs
# Gate check runs before git sync to avoid wasted fetches
0 6 * * * cd $PROJECT_ROOT && source .envrc && ./scripts/check-bot-prs.sh && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/sync-target-repo.sh && ./scripts/with-lock.sh learnable-pattern-search -- $CLAUDE_BIN -p '/learnable-pattern-search 2d' --allowedTools '$CLAUDE_TOOLS' >> $LOG_DIR/learnable-pattern-search-cron.log 2>&1

# Check Signal messages (every 5 min, offset to avoid git lock contention with other jobs)
# Gate check runs before git sync to avoid wasted fetches (288 runs/day, most exit early)
1,6,11,16,21,26,31,36,41,46,51,56 * * * * cd $PROJECT_ROOT && source .envrc && ./scripts/check-signal-messages.sh && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/with-lock.sh check-signal -- $CLAUDE_BIN -p '/check-signal' --allowedTools '$CLAUDE_TOOLS' >> $LOG_DIR/check-signal-cron.log 2>&1

# Update best practices from upstream Chromium docs (monthly, 1st of each month)
15 4 1 * * cd $PROJECT_ROOT && source .envrc && git fetch origin && git checkout $BOT_REPO_BRANCH && git reset --hard origin/$BOT_REPO_BRANCH && ./scripts/sync-target-repo.sh && ./scripts/with-lock.sh update-best-practices -- $CLAUDE_BIN -p '/update-best-practices' --allowedTools '$CLAUDE_TOOLS' >> $LOG_DIR/update-best-practices-cron.log 2>&1

$(if [ "$SYNC_REPO_ENABLED" = "true" ] && [ -n "$SYNC_REPO_PATH" ]; then
cat <<SYNC
# Sync repo origin/$BOT_DEFAULT_BRANCH from upstream/$BOT_DEFAULT_BRANCH (daily at 00:01)
1 0 * * * cd $SYNC_REPO_PATH && git fetch upstream && git push origin upstream/$BOT_DEFAULT_BRANCH:$BOT_DEFAULT_BRANCH && git fetch origin >> $LOG_DIR/sync-repo-cron.log 2>&1
SYNC
fi)

# === end $CRON_MARKER ===
EOF
)

# Extract existing crontab, stripping every previous block for this project --
# including one written under the repo's former name, which would otherwise
# stay installed and run a second, older copy of every job.
EXISTING=$(crontab -l 2>/dev/null || true)
CLEANED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" <<< "$EXISTING")

# Combine preserved entries with new block
NEW_CRONTAB=$(printf '%s\n%s\n' "$CLEANED" "$CRON_JOBS" | sed '/^$/N;/^\n$/d')

echo "$NEW_CRONTAB" | crontab -

echo "Cron jobs installed successfully."
echo ""
echo "Current schedule:"
echo "  Weekdays (Mon-Fri):"
echo "    08:10 - run.sh (3 iterations)"
echo "    07:45 - PRD/backlog sync (no agent)"
echo "    13:00, 20:00 - /review-prs"
echo "  Weekends (Sat-Sun):"
echo "    14:10 - run.sh (3 iterations)"
echo "    11:45 - PRD/backlog sync (no agent)"
echo "    12:00 - /review-prs"
echo "  Daily:"
echo "    06:00 - /learnable-pattern-search"
echo "    Every 5 min at :01,:06,...,:56 - /check-signal (only if messages pending)"
echo "    1st of month, 04:15 - /update-best-practices"
echo "    00:01 - sync repo origin/$BOT_DEFAULT_BRANCH from upstream (if enabled)"
echo ""
crontab -l
