#!/bin/bash
# Idempotent cron job setup for brave-dev-loop.
#
# The jobs are per-project: this script builds the crontab blocks, but what goes
# in them comes from projects/<profile>/schedules.sh (projects/default/ when the
# profile has no file of its own). Schedule changes are made there and
# committed to source control.
#
# A project's jobs are split into groups -- run, review, maintenance -- and each
# group is a crontab block of its own, so a machine can install only the jobs
# that run.sh, or only the ones that review, without the other set.
#
#   ./scripts/sync-schedules.sh                    install/update every group
#   ./scripts/sync-schedules.sh --group review     install/update one group only
#   ./scripts/sync-schedules.sh --print [...]      print the blocks, touch no crontab
#
# The inverse is remove-schedules.sh, which takes the same --group.

set -e

PRINT_ONLY=false
GROUP=all
while [ $# -gt 0 ]; do
  case "$1" in
    --print) PRINT_ONLY=true ;;
    --group)
      [ $# -ge 2 ] || { echo "Error: --group needs a value (run, review, maintenance or all)." >&2; exit 1; }
      GROUP="$2"
      shift
      ;;
    *) echo "Usage: sync-schedules.sh [--print] [--group run|review|maintenance|all]" >&2; exit 1 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/cron-blocks.sh"
source "$SCRIPT_DIR/lib/cron-jobs.sh"

if [ "$GROUP" = "all" ]; then
  GROUPS_TO_SYNC=("${BOT_CRON_GROUPS[@]}")
else
  case " ${BOT_CRON_GROUPS[*]} " in
    *" $GROUP "*) GROUPS_TO_SYNC=("$GROUP") ;;
    *) echo "Error: unknown group '$GROUP' (run, review, maintenance or all)." >&2; exit 1 ;;
  esac
fi

CLAUDE_BIN="$BOT_CLAUDE_BIN"
CLAUDE_TOOLS="$BOT_AGENT_TOOLS"
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

# The jobs file defines one function per group (schedule_run, schedule_review,
# schedule_maintenance), each printing that group's crontab lines. A group it
# does not define is a group this project has no jobs in.
source "$SCHEDULE_FILE"

# The marker carries the project name, and the group, so several deployments
# can share one crontab and one project's groups can be installed apart:
# installing one block leaves every other block alone. cron-blocks.sh owns the
# spelling, and the older spellings it has to strip alongside it.
build_block() {
  local group="$1" marker jobs
  marker=$(bot_cron_marker "$BOT_PROJECT_NAME" "$group")
  jobs=$("schedule_$group")
  cat <<BLOCK
# === $marker scheduled jobs ===
# Managed by sync-schedules.sh - do not edit manually
# Jobs: ${SCHEDULE_FILE#$PROJECT_ROOT/}
SHELL=/bin/bash
PATH=$CLAUDE_BIN_DIR:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
$jobs

# === end $marker ===
BLOCK
}

# Only the groups this project has jobs in.
BLOCKS=""
INSTALLED_GROUPS=()
for g in "${GROUPS_TO_SYNC[@]}"; do
  declare -F "schedule_$g" >/dev/null || continue
  BLOCKS="${BLOCKS:+$BLOCKS

}$(build_block "$g")"
  INSTALLED_GROUPS+=("$g")
done

if [ ${#INSTALLED_GROUPS[@]} -eq 0 ]; then
  echo "Error: ${SCHEDULE_FILE#$PROJECT_ROOT/} defines no '$GROUP' jobs for $BOT_PROJECT_NAME." >&2
  exit 1
fi

if $PRINT_ONLY; then
  echo "$BLOCKS"
  exit 0
fi

EXISTING=$(crontab -l 2>/dev/null || true)

# One group out of a block written before groups existed cannot be split off
# it, and replacing the block would silently drop the other groups' jobs.
if [ "$GROUP" != "all" ] && bot_has_ungrouped_cron_block "$BOT_PROJECT_NAME" <<< "$EXISTING"; then
  echo "Error: $BOT_PROJECT_NAME has a single crontab block from before jobs were grouped." >&2
  echo "Run 'make schedules' to replace it with one block per group, or 'make remove-schedules' to drop it." >&2
  exit 1
fi

# Strip the previous blocks for what is being installed -- including one
# written under the repo's former name, which would otherwise stay installed
# and run a second, older copy of every job. Another project's block is not
# this project's to touch, and is left where it is.
if [ "$GROUP" = "all" ]; then
  CLEANED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" <<< "$EXISTING")
else
  CLEANED=$(bot_strip_cron_blocks "$BOT_PROJECT_NAME" "$GROUP" <<< "$EXISTING")
fi

# Combine preserved entries with the new blocks
NEW_CRONTAB=$(printf '%s\n%s\n' "$CLEANED" "$BLOCKS" | sed '/^$/N;/^\n$/d')

echo "$NEW_CRONTAB" | crontab -

echo "Cron jobs installed for $BOT_PROJECT_NAME (${INSTALLED_GROUPS[*]}; ${SCHEDULE_FILE#$PROJECT_ROOT/})."
echo ""
echo "$BLOCKS"
echo ""
echo "Run 'make view-schedules' for a summary, or 'crontab -l' for every project's block."
