#!/bin/bash
# Idempotent cron job setup for brave-dev-loop.
#
# The jobs are per-project: this script builds the crontab block, but what goes
# in it comes from projects/<profile>/schedules.sh (projects/default/ when the
# profile has no file of its own). Schedule changes are made there and
# committed to source control.
#
#   ./scripts/sync-schedules.sh            install/update this project's block
#   ./scripts/sync-schedules.sh --print    print the block, touch no crontab

set -e

PRINT_ONLY=false
if [ "${1:-}" = "--print" ]; then
  PRINT_ONLY=true
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/cron-blocks.sh"
source "$SCRIPT_DIR/lib/cron-jobs.sh"

CLAUDE_BIN="$BOT_CLAUDE_BIN"
CLAUDE_TOOLS="Bash,Read,Glob,Grep,Write,Edit,Task,WebFetch"
LOG_DIR="$PROJECT_ROOT/logs"
$PRINT_ONLY || mkdir -p "$LOG_DIR"

# Derive the PATH from CLAUDE_BIN's directory
CLAUDE_BIN_DIR=$(dirname "$CLAUDE_BIN")

# Resolve sync repo path from config (optional — only for projects that sync from upstream)
SYNC_REPO_ENABLED=$(bot_config_bool '.schedules.syncRepo')
SYNC_REPO_PATH=$(bot_config '.schedules.syncRepoPath')

# Bot repo's own default branch (for cron git checkout/reset)
BOT_REPO_BRANCH=$(bot_config '.project.botRepoBranch')
BOT_REPO_BRANCH="${BOT_REPO_BRANCH:-master}"

# This project's jobs. A profile without a schedules.sh gets the generic set,
# so adding a project is still just a profile directory.
SCHEDULE_FILE="$BOT_PROFILE_DIR/schedules.sh"
if [ ! -f "$SCHEDULE_FILE" ]; then
  SCHEDULE_FILE="$PROJECT_ROOT/projects/default/schedules.sh"
fi
if [ ! -f "$SCHEDULE_FILE" ]; then
  echo "Error: no schedules.sh for profile '$BOT_PROFILE', and no default one either." >&2
  exit 1
fi

# The marker carries the project name so several deployments can share one
# crontab: installing one project's jobs leaves every other project's block
# alone. cron-blocks.sh owns its spelling, and the older spellings it has to
# strip alongside it.
CRON_MARKER=$(bot_cron_marker "$BOT_PROJECT_NAME")

CRON_JOBS=$(cat <<EOF
# === $CRON_MARKER scheduled jobs ===
# Managed by sync-schedules.sh - do not edit manually
# Jobs: ${SCHEDULE_FILE#$PROJECT_ROOT/}
SHELL=/bin/bash
PATH=$CLAUDE_BIN_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
$(source "$SCHEDULE_FILE")

# === end $CRON_MARKER ===
EOF
)

if $PRINT_ONLY; then
  echo "$CRON_JOBS"
  exit 0
fi

# Extract existing crontab, stripping every previous block for this project --
# including one written under the repo's former name, which would otherwise
# stay installed and run a second, older copy of every job. Another project's
# block is not this project's to touch, and is left where it is.
EXISTING=$(crontab -l 2>/dev/null || true)
CLEANED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" <<< "$EXISTING")

# Combine preserved entries with new block
NEW_CRONTAB=$(printf '%s\n%s\n' "$CLEANED" "$CRON_JOBS" | sed '/^$/N;/^\n$/d')

echo "$NEW_CRONTAB" | crontab -

echo "Cron jobs installed for $BOT_PROJECT_NAME (${SCHEDULE_FILE#$PROJECT_ROOT/})."
echo ""
echo "$CRON_JOBS"
echo ""
echo "Run 'make view-schedules' for a summary, or 'crontab -l' for every project's block."
