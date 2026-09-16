#!/bin/bash
# Display a human-readable summary of this project's scheduled tasks.
#
# Reads the block sync-schedules.sh would install rather than the script that
# installs it: the jobs live in projects/<profile>/schedules.sh, and rendering
# them is the only way to know which file this deployment uses and what the
# lines in it come out as.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

BLOCK=$("$SCRIPT_DIR/sync-schedules.sh" --print)
SCHEDULE_FILE=$(sed -n 's/^# Jobs: //p' <<< "$BLOCK")

# Colors (disable if not a terminal)
if [ -t 1 ]; then
  BOLD='\033[1m'
  DIM='\033[2m'
  CYAN='\033[36m'
  GREEN='\033[32m'
  RESET='\033[0m'
else
  BOLD='' DIM='' CYAN='' GREEN='' RESET=''
fi

cron_to_human() {
  local min="$1" hour="$2" dom="$3" mon="$4" dow="$5"

  local freq_part=""
  local time_part=""

  # Determine frequency
  if [[ "$dom" != "*" && "$mon" == "*" ]]; then
    freq_part="Monthly (${dom}st)"
  elif [[ "$min" == *","* && "$hour" == "*" ]]; then
    local count
    count=$(echo "$min" | tr ',' '\n' | wc -l | tr -d ' ')
    local interval=$((60 / count))
    freq_part="Every ${interval} min"
  elif [[ "$min" == "*/"* ]]; then
    freq_part="Every ${min#*/} min"
  elif [[ "$hour" == "*/"* ]]; then
    freq_part="Every ${hour#*/}h"
  else
    freq_part="Daily"
  fi

  # Determine times
  if [[ "$hour" != "*" && "$hour" != "*/"* ]]; then
    local times=""
    # Handle comma-separated minutes (shouldn't happen with hours set, but be safe)
    local first_min
    first_min=$(echo "$min" | cut -d',' -f1)
    for h in $(echo "$hour" | tr ',' ' '); do
      times="${times:+$times, }$(printf '%02d:%02d' "$h" "$first_min")"
    done
    time_part="at $times"
  elif [[ "$min" == *","* && "$hour" == "*" ]]; then
    local first_min
    first_min=$(echo "$min" | cut -d',' -f1)
    time_part="at :$(printf '%02d' "$first_min") past each hour"
  fi

  echo "${freq_part}${time_part:+ $time_part}"
}

echo -e "${BOLD}Scheduled Tasks — $BOT_PROJECT_NAME${RESET}"
echo -e "${DIM}Source: ${SCHEDULE_FILE:-projects/default/schedules.sh}${RESET}"
echo ""

comment=""
gate=""

while IFS= read -r line; do
  # Skip the block's own header: markers, the managed-by note, the jobs file,
  # and the environment the jobs run under.
  if [[ "$line" =~ ^(SHELL|PATH)= ]] || [[ "$line" == "# === "* ]] ||
     [[ "$line" == *"do not edit"* ]] || [[ "$line" == "# Jobs: "* ]]; then
    continue
  fi

  # Collect comment lines
  if [[ "$line" =~ ^#\  ]]; then
    local_stripped="${line#\# }"
    if [[ "$local_stripped" == Gate* ]]; then
      gate="$local_stripped"
    elif [ -z "$comment" ]; then
      comment="$local_stripped"
    else
      comment="$comment | $local_stripped"
    fi
    continue
  fi

  # Skip empty lines (reset comment state)
  if [ -z "$line" ]; then
    comment=""
    gate=""
    continue
  fi

  # Parse cron line into 5 schedule fields + command
  if [[ "$line" =~ ^([0-9,*/]+)[[:space:]]+([0-9,*/]+)[[:space:]]+([0-9,*/]+)[[:space:]]+([0-9,*/]+)[[:space:]]+([0-9,*/]+)[[:space:]]+(.*) ]]; then
    cron_min="${BASH_REMATCH[1]}"
    cron_hour="${BASH_REMATCH[2]}"
    cron_dom="${BASH_REMATCH[3]}"
    cron_mon="${BASH_REMATCH[4]}"
    cron_dow="${BASH_REMATCH[5]}"
    command="${BASH_REMATCH[6]}"
  else
    comment=""
    gate=""
    continue
  fi

  # Extract the task name from the command
  task_name=""
  if [[ "$command" == *"./run.sh"* ]]; then
    # Extract iteration count: look for number after run.sh
    if [[ "$command" =~ run\.sh[[:space:]]+([0-9]+) ]]; then
      task_name="run.sh (${BASH_REMATCH[1]} iterations)"
    else
      task_name="run.sh"
    fi
  elif [[ "$command" =~ -p\ \'(/[a-z-]+) ]]; then
    task_name="${BASH_REMATCH[1]}"
  elif [[ "$command" == *"git push origin"* ]]; then
    task_name="sync repo upstream→origin"
  else
    task_name="(unknown)"
  fi

  # Convert cron schedule to human-readable
  human=$(cron_to_human "$cron_min" "$cron_hour" "$cron_dom" "$cron_mon" "$cron_dow")
  cron_expr="$cron_min $cron_hour $cron_dom $cron_mon $cron_dow"

  # Print the entry
  echo -e "  ${GREEN}$task_name${RESET}"
  echo -e "    ${CYAN}Schedule:${RESET} $human"
  echo -e "    ${CYAN}Cron:${RESET}     ${DIM}$cron_expr${RESET}"
  if [ -n "$gate" ]; then
    echo -e "    ${CYAN}Gate:${RESET}     ${DIM}${gate#Gate check *}${RESET}"
  fi
  if [ -n "$comment" ]; then
    echo -e "    ${CYAN}Note:${RESET}     ${DIM}$comment${RESET}"
  fi
  echo ""

  comment=""
  gate=""
done <<< "$BLOCK"

# Show currently running jobs
# Detect via two methods:
# 1. Lock files (with-lock.sh jobs) — try flock; if we can't acquire, it's held
# 2. Process scan (run.sh) — run.sh releases its lock before children finish,
#    so we look for timeout-tree.sh processes spawned from the bot directory
BOT_DIR_ABS="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_DIR="$BOT_DIR_ABS/.ignore"
# The locks this project's jobs actually take, read off the block itself — a
# project that does not run a job has no lock for it to report on.
mapfile -t LOCK_NAMES < <(grep -o 'with-lock\.sh [a-z-]*' <<< "$BLOCK" | awk '{print $2}' | sort -u)

has_running=false

# Check run.sh — look for timeout-tree.sh processes from this bot directory
run_pid=$(ps -eo pid,args | grep "[t]imeout-tree.sh.*claude.*workflow" | head -1 | awk '{print $1}')
if [ -n "$run_pid" ]; then
  has_running=true
  echo -e "${BOLD}Running Jobs${RESET}"
  elapsed=$(ps -o etime= -p "$run_pid" 2>/dev/null | tr -d ' ')
  # Extract story ID from the command args
  story=""
  args=$(ps -o args= -p "$run_pid" 2>/dev/null)
  if [[ "$args" =~ story\ (US-[0-9]+) ]]; then
    story=" (${BASH_REMATCH[1]})"
  fi
  echo -e "  ${GREEN}run.sh${story}${RESET}  running for ${CYAN}${elapsed}${RESET}  (pid $run_pid)"
fi

# Check with-lock.sh jobs via lock files
for name in "${LOCK_NAMES[@]}"; do
  lockfile="$LOCK_DIR/.${name}.lock"
  [ -f "$lockfile" ] || continue

  # Try to acquire the lock — if we can't, something is holding it
  if command -v flock >/dev/null 2>&1 && ! ( flock -n 9 ) 9<"$lockfile" 2>/dev/null; then
    if [ "$has_running" = false ]; then
      echo -e "${BOLD}Running Jobs${RESET}"
      has_running=true
    fi

    # Find the with-lock.sh process
    pid=$(ps -eo pid,args | grep "[w]ith-lock\\.sh $name " | head -1 | awk '{print $1}')
    if [ -n "$pid" ]; then
      elapsed=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
      echo -e "  ${GREEN}$name${RESET}  running for ${CYAN}${elapsed}${RESET}  (pid $pid)"
    else
      # Lock held but can't find process — find timeout-tree child instead
      pid=$(ps -eo pid,args | grep "[t]imeout-tree.sh.*$name" | head -1 | awk '{print $1}')
      if [ -n "$pid" ]; then
        elapsed=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
        echo -e "  ${GREEN}$name${RESET}  running for ${CYAN}${elapsed}${RESET}  (pid $pid)"
      else
        echo -e "  ${GREEN}$name${RESET}  running  ${DIM}(pid unknown)${RESET}"
      fi
    fi
  fi
done

if [ "$has_running" = false ]; then
  echo -e "${BOLD}Running Jobs${RESET}"
  echo -e "  ${DIM}(none)${RESET}"
fi
echo ""
