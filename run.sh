#!/bin/bash
# Long-running AI agent loop
# Usage: ./run.sh [max_iterations] [tui] [--agent claude|codex|cursor|bravebot] [--model model]
#                 [--agent-bin path] [--comparison-run [--comparison-agent name]
#                 [--comparison-agent-bin path] [--comparison-model name]
#                 [--comparison-branch name]] [extra_prompt_info...]
#        ./run.sh --status     # what is running in this bot directory
#
# Several runs can share one bot directory when bot.maxConcurrentRuns is above
# 1: each takes a numbered *run slot* and keeps its own lock, run state, logs
# and story claim. See docs/concurrent-runs.md. At the default of 1 this is
# exactly the single-run loop it has always been, using the same paths.
#
# Agent selection precedence (highest wins):
#   1. --agent <name> CLI flag
#   2. BOT_AGENT env var
#   3. bot.agent in config.json
#   4. "claude" default
#
# Model selection:
#   --model <name> overrides bot.claudeModel, bot.codexModel, or bot.cursorModel for the selected
#   agent. bravebot has no config key, so --model is the only way to name a model for it.
#
# Binary selection:
#   --agent-bin <path> runs the selected agent from that path instead of the configured
#   one. bravebot has no config key of its own, so this is how a locally built binary is
#   used: ./run.sh --agent bravebot --agent-bin target/release/bravebot
#
# Comparison runs (off unless asked for):
#   --comparison-run works every story a second time with another tool, in a worktree of
#   its own, then has a third run critique the first against it and file issues for the
#   gaps. It never pushes, opens a PR, or comments anywhere. The comparison and evaluator
#   runs default to claude; --comparison-agent / --comparison-agent-bin /
#   --comparison-model override that, and --comparison-branch names the branch the
#   comparison works in. See docs/comparison-runs.md.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/scripts/lib/run-slots.sh"

# --status answers "what is running here?" without starting anything.
if [ "${1:-}" = "--status" ]; then
  exec "$SCRIPT_DIR/scripts/run-status.sh" "${@:2}"
fi

# Parse arguments
MAX_ITERATIONS=10

USE_TUI=false
EXTRA_PROMPT=""
PAST_TUI=false
CLI_AGENT=""
CLI_MODEL=""
CLI_AGENT_BIN=""
COMPARISON_RUN=false
CLI_COMPARISON_AGENT=""
CLI_COMPARISON_AGENT_BIN=""
CLI_COMPARISON_MODEL=""
CLI_COMPARISON_BRANCH=""
EXPECT_AGENT_VALUE=false
EXPECT_MODEL_VALUE=false
EXPECT_AGENT_BIN_VALUE=false
EXPECT_COMPARISON_AGENT_VALUE=false
EXPECT_COMPARISON_AGENT_BIN_VALUE=false
EXPECT_COMPARISON_MODEL_VALUE=false
EXPECT_COMPARISON_BRANCH_VALUE=false

for arg in "$@"; do
  if [ "$EXPECT_AGENT_VALUE" = true ]; then
    CLI_AGENT="$arg"
    EXPECT_AGENT_VALUE=false
    continue
  fi
  if [ "$EXPECT_COMPARISON_AGENT_VALUE" = true ]; then
    CLI_COMPARISON_AGENT="$arg"
    EXPECT_COMPARISON_AGENT_VALUE=false
    continue
  fi
  if [ "$EXPECT_COMPARISON_AGENT_BIN_VALUE" = true ]; then
    CLI_COMPARISON_AGENT_BIN="$arg"
    EXPECT_COMPARISON_AGENT_BIN_VALUE=false
    continue
  fi
  if [ "$EXPECT_COMPARISON_MODEL_VALUE" = true ]; then
    CLI_COMPARISON_MODEL="$arg"
    EXPECT_COMPARISON_MODEL_VALUE=false
    continue
  fi
  if [ "$EXPECT_COMPARISON_BRANCH_VALUE" = true ]; then
    CLI_COMPARISON_BRANCH="$arg"
    EXPECT_COMPARISON_BRANCH_VALUE=false
    continue
  fi
  if [ "$EXPECT_MODEL_VALUE" = true ]; then
    CLI_MODEL="$arg"
    EXPECT_MODEL_VALUE=false
    continue
  fi
  if [ "$EXPECT_AGENT_BIN_VALUE" = true ]; then
    CLI_AGENT_BIN="$arg"
    EXPECT_AGENT_BIN_VALUE=false
    continue
  fi
  # --agent is recognized regardless of position (before or after `tui`).
  if [[ "$arg" == "--agent" ]]; then
    EXPECT_AGENT_VALUE=true
    continue
  elif [[ "$arg" == --agent=* ]]; then
    CLI_AGENT="${arg#--agent=}"
    continue
  elif [[ "$arg" == "--agent-bin" ]]; then
    EXPECT_AGENT_BIN_VALUE=true
    continue
  elif [[ "$arg" == --agent-bin=* ]]; then
    CLI_AGENT_BIN="${arg#--agent-bin=}"
    continue
  elif [[ "$arg" == "--model" ]]; then
    EXPECT_MODEL_VALUE=true
    continue
  elif [[ "$arg" == --model=* ]]; then
    CLI_MODEL="${arg#--model=}"
    continue
  elif [[ "$arg" == "--comparison-run" ]]; then
    COMPARISON_RUN=true
    continue
  elif [[ "$arg" == "--comparison-agent" ]]; then
    EXPECT_COMPARISON_AGENT_VALUE=true
    continue
  elif [[ "$arg" == --comparison-agent=* ]]; then
    CLI_COMPARISON_AGENT="${arg#--comparison-agent=}"
    continue
  elif [[ "$arg" == "--comparison-agent-bin" ]]; then
    EXPECT_COMPARISON_AGENT_BIN_VALUE=true
    continue
  elif [[ "$arg" == --comparison-agent-bin=* ]]; then
    CLI_COMPARISON_AGENT_BIN="${arg#--comparison-agent-bin=}"
    continue
  elif [[ "$arg" == "--comparison-model" ]]; then
    EXPECT_COMPARISON_MODEL_VALUE=true
    continue
  elif [[ "$arg" == --comparison-model=* ]]; then
    CLI_COMPARISON_MODEL="${arg#--comparison-model=}"
    continue
  elif [[ "$arg" == "--comparison-branch" ]]; then
    EXPECT_COMPARISON_BRANCH_VALUE=true
    continue
  elif [[ "$arg" == --comparison-branch=* ]]; then
    CLI_COMPARISON_BRANCH="${arg#--comparison-branch=}"
    continue
  fi
  if [ "$PAST_TUI" = true ]; then
    # Everything else after 'tui' is extra prompt info
    if [ -n "$EXTRA_PROMPT" ]; then
      EXTRA_PROMPT="$EXTRA_PROMPT $arg"
    else
      EXTRA_PROMPT="$arg"
    fi
  elif [[ "$arg" =~ ^[0-9]+$ ]]; then
    MAX_ITERATIONS="$arg"
  elif [[ "$arg" == "tui" ]]; then
    USE_TUI=true
    PAST_TUI=true
  fi
done

if [ "$EXPECT_AGENT_VALUE" = true ]; then
  echo "Error: --agent requires a value (expected: claude | codex | cursor | bravebot)" >&2
  exit 1
fi
if [ "$EXPECT_MODEL_VALUE" = true ]; then
  echo "Error: --model requires a value" >&2
  exit 1
fi
if [ "$EXPECT_AGENT_BIN_VALUE" = true ]; then
  echo "Error: --agent-bin requires a value" >&2
  exit 1
fi
if [ "$EXPECT_COMPARISON_AGENT_VALUE" = true ]; then
  echo "Error: --comparison-agent requires a value (expected: claude | codex | cursor | bravebot)" >&2
  exit 1
fi
if [ "$EXPECT_COMPARISON_AGENT_BIN_VALUE" = true ]; then
  echo "Error: --comparison-agent-bin requires a value" >&2
  exit 1
fi
if [ "$EXPECT_COMPARISON_MODEL_VALUE" = true ]; then
  echo "Error: --comparison-model requires a value" >&2
  exit 1
fi
if [ "$EXPECT_COMPARISON_BRANCH_VALUE" = true ]; then
  echo "Error: --comparison-branch requires a value" >&2
  exit 1
fi
# A comparison flag with no --comparison-run is a run that silently does not
# compare anything, which is the one outcome nobody wants from typing them.
if [ "$COMPARISON_RUN" != true ] &&
   { [ -n "$CLI_COMPARISON_AGENT" ] || [ -n "$CLI_COMPARISON_AGENT_BIN" ] ||
     [ -n "$CLI_COMPARISON_MODEL" ] || [ -n "$CLI_COMPARISON_BRANCH" ]; }; then
  echo "Error: --comparison-* configures a comparison run, but --comparison-run was not given." >&2
  exit 1
fi

source "$SCRIPT_DIR/scripts/lib/load-config.sh"
source "$SCRIPT_DIR/scripts/lib/git-identity.sh"
source "$SCRIPT_DIR/scripts/lib/terminal-title.sh"

# This run titles its own tab, with the issue and PR numbers an operator
# switches tabs to find. Claude would otherwise overwrite that every turn with
# a summary of its conversation.
export CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1

# Pin this run to the bot's GitHub identity before anything can touch GitHub.
bot_export_identity_env "$BOT_SSH_KEY_PATH" "$BOT_GH_ACCOUNT" || exit 1

# A profile nobody chose produces wrong work rather than no work — generic
# validations, the wrong docs — and does it silently, iteration after
# iteration. Stop instead, saying which line to change.
if PROFILE_MISMATCH=$(bot_profile_mismatch); then
  echo "Error: $PROFILE_MISMATCH" >&2
  exit 1
fi

# --- Concurrency ----------------------------------------------------------
# Running two agents against one working tree destroys work, so concurrency is
# allowed only for a profile that gives every story its own git worktree.
bot_validate_concurrency \
  "$BOT_MAX_CONCURRENT_RUNS" "$BOT_PROFILE" "$BOT_PROFILE_WORKTREES" || exit 1

# Take a run slot. Everything this run cannot share with another — run state,
# iteration logs, the story it is working — is keyed to the slot number.
SLOT_RC=0
bot_acquire_run_slot "$SCRIPT_DIR" "$BOT_MAX_CONCURRENT_RUNS" || SLOT_RC=$?
case $SLOT_RC in
  0) ;;
  1)
    if [ "$BOT_MAX_CONCURRENT_RUNS" = "1" ]; then
      echo "Another run.sh is already running. Exiting."
    else
      echo "All $BOT_MAX_CONCURRENT_RUNS run slots are busy. Exiting."
    fi
    printf '%s' "$BOT_SLOT_BUSY_REPORT"
    exit 0 ;;
  *) echo "Could not acquire a run slot. Exiting." >&2; exit 1 ;;
esac

BOT_RUN_PID=$$
export BOT_RUN_SLOT BOT_RUN_PID
CURRENT_STORY_ID=""
TITLE_WATCH_PID=""

# CLI --agent flag has the final say (overrides env + config)
if [ -n "$CLI_AGENT" ]; then
  BOT_AGENT="$CLI_AGENT"
fi
case "$BOT_AGENT" in
  claude|codex|cursor|bravebot) ;;
  *)
    echo "Error: unsupported agent '$BOT_AGENT' (expected: claude | codex | cursor | bravebot)" >&2
    exit 1
    ;;
esac
if [ -n "$CLI_MODEL" ]; then
  if [ "$BOT_AGENT" = "codex" ]; then
    BOT_CODEX_MODEL="$CLI_MODEL"
  elif [ "$BOT_AGENT" = "cursor" ]; then
    BOT_CURSOR_MODEL="$CLI_MODEL"
  elif [ "$BOT_AGENT" = "bravebot" ]; then
    BOT_BRAVEBOT_MODEL="$CLI_MODEL"
  else
    BOT_CLAUDE_MODEL="$CLI_MODEL"
  fi
fi
# Applied to whichever agent is selected, so a locally built binary needs no config edit.
if [ -n "$CLI_AGENT_BIN" ]; then
  if [ "$BOT_AGENT" = "codex" ]; then
    BOT_CODEX_BIN="$CLI_AGENT_BIN"
  elif [ "$BOT_AGENT" = "cursor" ]; then
    BOT_CURSOR_BIN="$CLI_AGENT_BIN"
  elif [ "$BOT_AGENT" = "bravebot" ]; then
    BOT_BRAVEBOT_BIN="$CLI_AGENT_BIN"
  else
    BOT_CLAUDE_BIN="$CLI_AGENT_BIN"
  fi
fi

# The comparison and evaluator runs default to claude whatever the base agent is:
# the point is a second opinion from a different tool, and claude is the loop's
# reference implementation of the workflow.
COMPARISON_AGENT="claude"
COMPARISON_AGENT_BIN=""
COMPARISON_MODEL=""
if [ "$COMPARISON_RUN" = true ]; then
  if [ -n "$CLI_COMPARISON_AGENT" ]; then
    COMPARISON_AGENT="$CLI_COMPARISON_AGENT"
  fi
  case "$COMPARISON_AGENT" in
    claude|codex|cursor|bravebot) ;;
    *)
      echo "Error: unsupported comparison agent '$COMPARISON_AGENT' (expected: claude | codex | cursor | bravebot)" >&2
      exit 1
      ;;
  esac
  case "$COMPARISON_AGENT" in
    codex)    COMPARISON_AGENT_BIN="$BOT_CODEX_BIN";    COMPARISON_MODEL="$BOT_CODEX_MODEL" ;;
    cursor)   COMPARISON_AGENT_BIN="$BOT_CURSOR_BIN";   COMPARISON_MODEL="$BOT_CURSOR_MODEL" ;;
    bravebot) COMPARISON_AGENT_BIN="$BOT_BRAVEBOT_BIN"; COMPARISON_MODEL="$BOT_BRAVEBOT_MODEL" ;;
    *)        COMPARISON_AGENT_BIN="$BOT_CLAUDE_BIN";   COMPARISON_MODEL="$BOT_CLAUDE_MODEL" ;;
  esac
  if [ -n "$CLI_COMPARISON_AGENT_BIN" ]; then
    COMPARISON_AGENT_BIN="$CLI_COMPARISON_AGENT_BIN"
  fi
  if [ -n "$CLI_COMPARISON_MODEL" ]; then
    COMPARISON_MODEL="$CLI_COMPARISON_MODEL"
  fi
fi

PRD_FILE="$SCRIPT_DIR/data/prd.json"
PROGRESS_FILE="$SCRIPT_DIR/data/progress.txt"
LOGS_DIR="$SCRIPT_DIR/logs"
# Slot 1 uses data/run-state.json, as it always has; further slots get their
# own file so two runs never share iteration bookkeeping. The agent reads
# BOT_RUN_STATE_FILE, so its update-prd-status.py calls land in the right one.
RUN_STATE_FILE=$(bot_slot_run_state_file "$SCRIPT_DIR" "$BOT_RUN_SLOT")
BOT_RUN_STATE_FILE="$RUN_STATE_FILE"
export BOT_RUN_STATE_FILE

# Resolve target repo path from config.json (required)
if [ -z "${BOT_TARGET_REPO_PATH:-}" ]; then
  echo "Error: project.targetRepoPath not set in config.json"
  exit 1
fi
GIT_REPO="$BOT_TARGET_REPO_DIR"
if [ ! -e "$GIT_REPO/.git" ]; then
  echo "Error: project.targetRepoPath '$BOT_TARGET_REPO_PATH' does not resolve to a git repo."
  echo "  Tried: $SCRIPT_DIR/$BOT_TARGET_REPO_PATH"
  echo "     and $(dirname "$SCRIPT_DIR")/$BOT_TARGET_REPO_PATH"
  exit 1
fi

# Hand the current story back so another run (or the next iteration) can take
# it. A run that is killed outright skips this, and that is fine: a claim only
# counts while its slot lock is held, which the kernel drops on death.
release_claim() {
  [ -n "$CURRENT_STORY_ID" ] || return 0
  "$SCRIPT_DIR/scripts/claims.py" release \
    --story "$CURRENT_STORY_ID" --slot "$BOT_RUN_SLOT" >/dev/null 2>&1 || true
  CURRENT_STORY_ID=""
}

# The PR watcher polls until a PR appears, so an iteration that ends first has
# to stop it, or it keeps polling for a story this run no longer holds.
stop_title_watch() {
  [ -n "$TITLE_WATCH_PID" ] || return 0
  kill "$TITLE_WATCH_PID" 2>/dev/null || true
  wait "$TITLE_WATCH_PID" 2>/dev/null || true
  TITLE_WATCH_PID=""
}

# What the agent does with a story in this status, in one line. Printed in the
# iteration banner and again when the iteration ends, so the terminal says what
# is about to happen (and what happens next) rather than only which story was
# picked. Each phrase is the goal stated at the top of the matching workflow doc.
story_next_step() {
  case "$1" in
    pending)   printf '%s' "implement and test the fix, then commit" ;;
    committed) printf '%s' "push the branch and open the PR" ;;
    pushed)    printf '%s' "check CI and reviews, address feedback, wait for the maintainer to merge" ;;
    merged)    printf '%s' "post-merge check for follow-up requests" ;;
    skipped)   printf '%s' "nothing — intentionally skipped" ;;
    invalid)   printf '%s' "nothing — closed as invalid" ;;
    *)         printf '%s' "follow docs/workflow-$1.md" ;;
  esac
}

cleanup_run() {
  stop_title_watch

  # Hand back the worktrees this session finished with, before the claim goes:
  # a run interrupted mid-iteration still holds one, and that claim is what
  # keeps clean-worktrees.py out of the worktree the agent was working in. No
  # age limit here — a session that has ended is done with all of them.
  if [ "$BOT_PROFILE_WORKTREES" = true ]; then
    python3 "$SCRIPT_DIR/scripts/clean-worktrees.py" >/dev/null || true
  fi

  release_claim
  bot_slot_meta_clear
  bot_release_lock

  # Under a worktree profile the main checkout is not this run's to touch:
  # every story works in its own worktree, and another run may be using the
  # checkout right now. Stashing and switching branches there would corrupt
  # whatever it is doing.
  if [ "$BOT_PROFILE_WORKTREES" = true ]; then
    return
  fi

  if [ -n "$GIT_REPO" ] && [ -d "$GIT_REPO/.git" ]; then
    echo ""
    echo "Switching back to $BOT_DEFAULT_BRANCH branch in $GIT_REPO..."
    cd "$GIT_REPO"
    git stash --include-untracked 2>/dev/null || true
    git checkout "$BOT_DEFAULT_BRANCH" 2>/dev/null || echo "Could not switch to $BOT_DEFAULT_BRANCH branch"
  fi
}

# Register cleanup function to run on exit
trap cleanup_run EXIT INT TERM HUP

# Initialize progress file if it doesn't exist
if [ ! -f "$PROGRESS_FILE" ]; then
  echo "# Progress Log" > "$PROGRESS_FILE"
  echo "Started: $(date)" >> "$PROGRESS_FILE"
  echo "---" >> "$PROGRESS_FILE"
fi

# Create logs directory if it doesn't exist
mkdir -p "$LOGS_DIR"

# Verify org members file exists (required for prompt injection protection)
ORG_MEMBERS_FILE="$SCRIPT_DIR/.ignore/org-members.txt"
if [ ! -f "$ORG_MEMBERS_FILE" ]; then
  echo "Error: Org members file not found at $ORG_MEMBERS_FILE"
  echo "This file is required for prompt injection protection."
  echo "Run 'make setup' or create it manually:"
  echo "  mkdir -p $SCRIPT_DIR/.ignore && gh api 'orgs/$BOT_ORG/members' --paginate | jq -r '.[].login' > $ORG_MEMBERS_FILE"
  exit 1
fi

if [ "$BOT_MAX_CONCURRENT_RUNS" -gt 1 ]; then
  echo "Run slot $BOT_RUN_SLOT of $BOT_MAX_CONCURRENT_RUNS (pid $$)"
fi
if [ "$BOT_AGENT" = "codex" ]; then
  echo "Starting Codex agent - Max iterations: $MAX_ITERATIONS"
elif [ "$BOT_AGENT" = "cursor" ]; then
  echo "Starting Cursor agent - Max iterations: $MAX_ITERATIONS"
elif [ "$BOT_AGENT" = "bravebot" ]; then
  echo "Starting bravebot agent - Max iterations: $MAX_ITERATIONS"
else
  echo "Starting Claude Code agent - Max iterations: $MAX_ITERATIONS"
fi
if [ "$COMPARISON_RUN" = true ]; then
  echo "Comparison runs enabled: every iteration is redone by $COMPARISON_AGENT and then critiqued."
  echo "  Findings are filed as issues in $BOT_ISSUE_REPO — see docs/comparison-runs.md"
fi
echo "Logs will be saved to: $LOGS_DIR"

# Fetch nightly version once per run (avoids redundant WebFetch in each iteration)
NIGHTLY_VERSION=$(python3 "$SCRIPT_DIR/scripts/get-nightly-version.py" 2>/dev/null || echo "")
if [ -n "$NIGHTLY_VERSION" ]; then
  echo "Nightly version: $NIGHTLY_VERSION"
else
  echo "Warning: Could not fetch nightly version (agent will fetch if needed)"
fi

# Reset run state at the start of each run. Operator settings
# (skipPushedTasks, merge backoff) are not per-slot: they are read from
# data/run-state.json so every slot honours the same configuration.
echo "Resetting run state for fresh start..."
"$SCRIPT_DIR/scripts/reset-run-state.sh" \
  --state-file "$RUN_STATE_FILE" --config-from "$SCRIPT_DIR/data/run-state.json"

# In auto mode the PRD is a cache — rebuild it from GitHub before selecting a
# task. Plain Python against the API; no agent is started, so this costs
# nothing. A curated PRD is authored, so a run leaves it alone entirely; its
# backlog top-up belongs to the scheduled job, at a time the operator picked.
# with-lock keeps two runs starting together from syncing at the same time;
# the second simply skips a refresh the first has just done.
if [ "$BOT_PRD_MODE" = "auto" ]; then
  "$SCRIPT_DIR/scripts/with-lock.sh" prd-sync --timeout 900 -- "$SCRIPT_DIR/scripts/sync-prd.sh"
fi

# Collect the worktrees of finished work before starting any. Each one carries a
# build directory of several gigabytes, and a run that is killed leaves its
# worktree behind, so they accumulate until the disk fills. A day is the age
# limit here: another run may be mid-iteration in a worktree it created minutes
# ago, and while clean-worktrees.py checks that too — a live run's claim, or
# work no remote has — the age bound means a fresh worktree is never a
# candidate in the first place. Python and git only; nothing here costs tokens.
if [ "$BOT_PROFILE_WORKTREES" = true ]; then
  python3 "$SCRIPT_DIR/scripts/clean-worktrees.py" --max-age-hours 24 >/dev/null \
    || echo "WARNING: worktree cleanup failed — continuing." >&2
fi

# Track both loop count (for max iterations) and work iterations (actual state changes)
loop_count=0
work_iteration=0

while [ $loop_count -lt $MAX_ITERATIONS ]; do
  ((++loop_count))

  # Initialize runId if it's null (start of new run)
  RUN_ID=$(jq -r '.runId // "null"' "$RUN_STATE_FILE" 2>/dev/null || echo "null")
  if [ "$RUN_ID" = "null" ]; then
    # Initialize new run with current timestamp
    RUN_ID=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    # Beside the target, so the mv below is a rename and not a cross-device
    # copy another run could read half of.
    TMP_RUN_STATE=$(mktemp "$(dirname "$RUN_STATE_FILE")/.run-state.XXXXXX")
    jq --arg runId "$RUN_ID" '.runId = $runId | .storiesCheckedThisRun = [] | .lastIterationHadStateChange = true' "$RUN_STATE_FILE" > "$TMP_RUN_STATE" && mv "$TMP_RUN_STATE" "$RUN_STATE_FILE"
  fi

  # Generate log file path for this iteration (needed by select-task.py).
  # The slot is part of the name: runIds are second-precision timestamps, so
  # two runs starting together would otherwise write to the same file.
  RUN_ID_SAFE=$(echo "$RUN_ID" | sed 's/[^a-zA-Z0-9-]/-/g')
  ITERATION_LOG="$LOGS_DIR/iteration-${RUN_ID_SAFE}-slot-${BOT_RUN_SLOT}-loop-${loop_count}.log"
  export BOT_RUN_ID="$RUN_ID"

  # Select next task — this is the gate check; exit early if no candidates
  TASK_JSON=""
  if [ -n "$EXTRA_PROMPT" ]; then
    TASK_JSON=$(python3 "$SCRIPT_DIR/scripts/select-task.py" \
      --prd "$PRD_FILE" \
      --run-state "$RUN_STATE_FILE" \
      --iteration-log "$ITERATION_LOG" \
      --claude-bin "$BOT_CLAUDE_BIN" \
      --slot "$BOT_RUN_SLOT" --run-pid "$BOT_RUN_PID" --run-id "$RUN_ID" \
      --extra-prompt "$EXTRA_PROMPT") || true
  else
    TASK_JSON=$(python3 "$SCRIPT_DIR/scripts/select-task.py" \
      --prd "$PRD_FILE" \
      --run-state "$RUN_STATE_FILE" \
      --iteration-log "$ITERATION_LOG" \
      --slot "$BOT_RUN_SLOT" --run-pid "$BOT_RUN_PID" --run-id "$RUN_ID") || true
  fi

  TASK_SELECTED=$(echo "$TASK_JSON" | jq -r '.selected // false' 2>/dev/null || echo "false")
  if [ "$TASK_SELECTED" != "true" ]; then
    REASON=$(echo "$TASK_JSON" | jq -r '.reason // "unknown"' 2>/dev/null || echo "unknown")
    echo "No more tasks to process: $REASON"
    break
  fi

  STORY_ID=$(echo "$TASK_JSON" | jq -r '.storyId')
  STORY_STATUS=$(echo "$TASK_JSON" | jq -r '.status')
  TIER_NAME=$(echo "$TASK_JSON" | jq -r '.tierName')
  STORY_TITLE=$(echo "$TASK_JSON" | jq -r '.title')
  STORY_DETAILS=$(echo "$TASK_JSON" | jq -c '.storyDetails')
  STORY_PRIORITY=$(echo "$TASK_JSON" | jq -r '.priority // "-"')
  STORY_ISSUE=$(echo "$TASK_JSON" | jq -r '.issueNumber // empty')
  STORY_PR_NUMBER=$(echo "$TASK_JSON" | jq -r '.prNumber // empty')
  STORY_PR_URL=$(echo "$TASK_JSON" | jq -r '.prUrl // empty')
  STORY_BRANCH=$(echo "$TASK_JSON" | jq -r '.branchName // empty')
  # Stories reference their issue by number only, so the link is built here.
  STORY_ISSUE_URL=""
  if [ -n "$STORY_ISSUE" ]; then
    STORY_ISSUE_URL="https://github.com/$BOT_ISSUE_REPO/issues/$STORY_ISSUE"
  fi

  # select-task.py claimed this story under the PRD lock; remember it so the
  # claim is handed back when the iteration ends or this run exits.
  CURRENT_STORY_ID="$STORY_ID"
  bot_slot_meta_set "storyId=$STORY_ID" "status=$STORY_STATUS" "runId=$RUN_ID" \
    "loop:num=$loop_count" "log=$ITERATION_LOG" "agent=$BOT_AGENT"
  bot_slot_heartbeat

  # Task confirmed — proceed with iteration setup
  # Store the current iteration log path in run-state.json
  TMP_RUN_STATE=$(mktemp "$(dirname "$RUN_STATE_FILE")/.run-state.XXXXXX")
  jq --arg logPath "$ITERATION_LOG" '.currentIterationLogPath = $logPath' "$RUN_STATE_FILE" > "$TMP_RUN_STATE" && mv "$TMP_RUN_STATE" "$RUN_STATE_FILE"

  # Check if last iteration had state change (default to true for first iteration)
  HAD_STATE_CHANGE=$(jq -r '.lastIterationHadStateChange // true' "$RUN_STATE_FILE" 2>/dev/null || echo "true")

  echo ""
  echo "==============================================================="
  # Only increment work iteration counter if there was actual state change
  if [ "$HAD_STATE_CHANGE" = "true" ]; then
    ((++work_iteration))
    echo "  Work Iteration $work_iteration (loop $loop_count of $MAX_ITERATIONS)"
  else
    echo "  Checking next task (work iteration $work_iteration, loop $loop_count of $MAX_ITERATIONS)"
    echo "  Previous check had no state change - continuing without incrementing work iteration"
  fi
  echo "---------------------------------------------------------------"
  echo "  Story:     $STORY_ID  $STORY_TITLE"
  echo "  Status:    $STORY_STATUS (tier $TIER_NAME, priority $STORY_PRIORITY)"
  [ -n "$STORY_ISSUE_URL" ] && echo "  Issue:     $STORY_ISSUE_URL"
  [ -n "$STORY_PR_URL" ] && echo "  PR:        $STORY_PR_URL"
  [ -n "$STORY_BRANCH" ] && echo "  Branch:    $STORY_BRANCH"
  echo "  Next step: $(story_next_step "$STORY_STATUS")"
  echo "  Workflow:  docs/workflow-$STORY_STATUS.md"
  echo "  Log:       $ITERATION_LOG"
  echo "==============================================================="

  bot_set_terminal_title \
    "$(bot_story_title "$STORY_ISSUE" "$STORY_PR_NUMBER" "$STORY_ID" "$STORY_TITLE")"
  # A story with no PR yet gets one partway through this iteration, and the tab
  # should say so from that moment rather than at the end of the iteration.
  if [ -z "$STORY_PR_NUMBER" ]; then
    "$SCRIPT_DIR/scripts/exec-clean.sh" "$SCRIPT_DIR/scripts/watch-pr-title.sh" \
      "$PRD_FILE" "$STORY_ID" "$STORY_ISSUE" "$STORY_TITLE" &
    TITLE_WATCH_PID=$!
  fi

  # Run Claude Code with the agent prompt
  # Use a temp file to capture output while allowing real-time streaming
  TEMP_OUTPUT=$(mktemp)
  # Codex and bravebot leave only their final agent message here (used for completion
  # detection, so file contents read mid-iteration can never trip the
  # <promise>COMPLETE</promise> check).
  TEMP_LAST_MSG=$(mktemp)

  # Change to the parent directory (brave-browser) so relative paths in .claude/CLAUDE.md work
  BRAVE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  cd "$BRAVE_ROOT"

  # Pre-generate session ID for monitoring/resume (Claude only — Codex has no --session-id).
  SESSION_ID=""
  if [ "$BOT_AGENT" = "claude" ]; then
    SESSION_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
    echo ""
    echo "To monitor this session (read-only): claude --resume $SESSION_ID"
    echo ""
  elif [ "$BOT_AGENT" = "cursor" ]; then
    echo ""
    echo "Cursor session: resume with 'cursor-agent resume' after the iteration."
    echo ""
  elif [ "$BOT_AGENT" = "bravebot" ]; then
    echo ""
    # bravebot keeps sessions per directory, and this one runs in BRAVE_ROOT.
    echo "bravebot session: resume with 'bravebot --continue' in $BRAVE_ROOT after the iteration."
    echo ""
  else
    echo ""
    echo "Codex session: resume with 'codex resume --last' after the iteration."
    echo ""
  fi

  # Build the agent prompt. All agents pick up project instructions from disk:
  #   - Claude reads .claude/CLAUDE.md from the bot dir.
  #   - Codex reads AGENTS.md (project) / ~/.codex/AGENTS.md (user).
  #   - Cursor reads AGENTS.md (project) + .cursor/rules.
  # We point them all at the same workflow docs via the prompt itself.
  BOT_DIRNAME=$(basename "$SCRIPT_DIR")
  BOT_CONFIG=$(cat "$SCRIPT_DIR/config.json")
  # A profile need not ship docs, and pointing an agent at a directory that is
  # not there costs it a search and teaches it the prompt lies.
  if [ -d "$BOT_PROFILE_DIR/docs" ]; then
    PROJECT_RULES="Project-specific rules live in ./$BOT_DIRNAME/projects/$BOT_PROFILE/docs/.
Where a workflow doc says a step is project-specific, read the named file there."
  else
    PROJECT_RULES="The '$BOT_PROFILE' profile ships no project-specific docs, so where a workflow
doc says a step is project-specific there is nothing further to read."
  fi
  # The profile's reading steps, rendered fresh by select-task.py rather than
  # read out of the story. Acceptance criteria are written when a story is
  # created, so a story older than a reading step would never see it; said
  # here, every iteration starts from what the project is reviewed against.
  RESEARCH=$(echo "$TASK_JSON" | jq -r '(.research // []) | map("- " + .) | join("\n")')
  READ_FIRST=""
  if [ -n "$RESEARCH" ]; then
    READ_FIRST="Read these first, whatever the story's acceptance criteria say:
$RESEARCH
"
  fi
  AGENT_PROMPT="You are working on story $STORY_ID (current status: $STORY_STATUS).
Follow ./$BOT_DIRNAME/docs/workflow-${STORY_STATUS}.md for the workflow.
Follow the general instructions in ./$BOT_DIRNAME/.claude/CLAUDE.md.

$READ_FIRST
$PROJECT_RULES

Story details:
$STORY_DETAILS

Bot config (from config.json — do NOT read this file):
$BOT_CONFIG"

  # Only said when it is true: a single-run deployment's prompt is unchanged.
  if [ "$BOT_MAX_CONCURRENT_RUNS" -gt 1 ]; then
    AGENT_PROMPT="$AGENT_PROMPT

Concurrency: this run holds slot $BOT_RUN_SLOT of $BOT_MAX_CONCURRENT_RUNS. Other runs may be working
other stories in this same bot directory right now.
- Your run state is $RUN_STATE_FILE (exported as BOT_RUN_STATE_FILE). Scripts pick
  it up automatically; do not read or write data/run-state.json directly.
- Never hand-edit data/prd.json. Use ./$BOT_DIRNAME/scripts/update-prd-status.py, which
  serializes writes — a hand edit will silently lose another run's update.
- Story $STORY_ID is claimed by this slot. Touch nothing outside it: not another
  story's worktree, branch, or PR.
- Append to data/progress.txt with ./$BOT_DIRNAME/scripts/append-progress.sh, not with >>."
  fi
  if [ -n "$NIGHTLY_VERSION" ]; then
    AGENT_PROMPT="$AGENT_PROMPT

Nightly version: $NIGHTLY_VERSION (use this for milestone names like '$NIGHTLY_VERSION - Nightly', do NOT fetch the release schedule)."
  fi
  if [ -n "$EXTRA_PROMPT" ]; then
    AGENT_PROMPT="$AGENT_PROMPT

Additional context: $EXTRA_PROMPT"
  fi

  # Log the prompt to the iteration log so it can be audited later
  if [ "$USE_TUI" != true ]; then
    jq -n --arg storyId "$STORY_ID" --arg status "$STORY_STATUS" --arg tier "$TIER_NAME" \
          --arg agent "$BOT_AGENT" --arg prompt "$AGENT_PROMPT" \
      '{"type":"prompt","storyId":$storyId,"status":$status,"tier":$tier,"agent":$agent,"prompt":$prompt}' >> "$ITERATION_LOG"
  fi

  # The second the base run starts. Only Claude is told its session id; every
  # other agent's session has to be found afterwards, and the search needs to
  # know how far back to look (scripts/find-agent-session.py).
  BASE_STARTED_AT=$(date +%s)

  # Run the agent from the bot directory so it picks up project instructions.
  #
  # Every stage goes through exec-clean.sh, which closes all inherited fds
  # above stdio. fd 200 is this run's slot lock, and a flock lives on the open
  # file description — so any child that inherits the fd keeps the slot held
  # after the run is gone. `somecmd 200>&-` does not fix that on bash 3.2 (the
  # macOS default): to apply the redirection bash first duplicates fd 200 to a
  # free fd near 10, and children inherit *that*. tee needs the same treatment
  # — it sits waiting on the pipe and outlives a killed run.
  if [ "$BOT_AGENT" = "codex" ]; then
    CODEX_MODEL_FLAG=""
    if [ -n "$BOT_CODEX_MODEL" ]; then
      CODEX_MODEL_FLAG="--model $BOT_CODEX_MODEL"
    fi
    if [ "$USE_TUI" = true ]; then
      # TUI mode: let codex own the terminal directly (no piping).
      "$SCRIPT_DIR/scripts/exec-clean.sh" --cd "$SCRIPT_DIR" "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CODEX_BIN $CODEX_MODEL_FLAG --dangerously-bypass-approvals-and-sandbox "$AGENT_PROMPT" || true
    else
      # Non-interactive: stream JSONL events to the iteration log; capture the
      # final agent message separately for the completion check.
      "$SCRIPT_DIR/scripts/exec-clean.sh" --cd "$SCRIPT_DIR" "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CODEX_BIN exec $CODEX_MODEL_FLAG --dangerously-bypass-approvals-and-sandbox --json --skip-git-repo-check --output-last-message "$TEMP_LAST_MSG" "$AGENT_PROMPT" </dev/null 2>&1 \
        | "$SCRIPT_DIR/scripts/exec-clean.sh" tee -a "$ITERATION_LOG" > "$TEMP_OUTPUT" || true
    fi
  elif [ "$BOT_AGENT" = "bravebot" ]; then
    BRAVEBOT_MODEL_FLAG=""
    if [ -n "$BOT_BRAVEBOT_MODEL" ]; then
      BRAVEBOT_MODEL_FLAG="--model $BOT_BRAVEBOT_MODEL"
    fi
    # A prompt on the command line is always a one-shot run: bravebot has no way to
    # start an interactive session with the prompt already in it. So TUI mode differs
    # only in owning the terminal, which is what lets it be watched and interrupted.
    if [ "$USE_TUI" = true ]; then
      "$SCRIPT_DIR/scripts/exec-clean.sh" --cd "$SCRIPT_DIR" "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_BRAVEBOT_BIN $BRAVEBOT_MODEL_FLAG --dangerously-skip-permissions "$AGENT_PROMPT" || true
    else
      # bravebot keeps stdout to the final reply alone and puts progress on stderr,
      # previews of quarantined content included. Since a preview can quote a file
      # that documents <promise>COMPLETE</promise>, stderr goes straight to the log
      # and only the reply reaches the file the completion check reads.
      "$SCRIPT_DIR/scripts/exec-clean.sh" --cd "$SCRIPT_DIR" "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_BRAVEBOT_BIN $BRAVEBOT_MODEL_FLAG --dangerously-skip-permissions "$AGENT_PROMPT" </dev/null 2>>"$ITERATION_LOG" \
        | "$SCRIPT_DIR/scripts/exec-clean.sh" tee -a "$ITERATION_LOG" > "$TEMP_LAST_MSG" || true
    fi
  elif [ "$BOT_AGENT" = "cursor" ]; then
    CURSOR_MODEL_FLAG=""
    if [ -n "$BOT_CURSOR_MODEL" ]; then
      CURSOR_MODEL_FLAG="--model $BOT_CURSOR_MODEL"
    fi
    if [ "$USE_TUI" = true ]; then
      # TUI mode: let cursor-agent own the terminal directly (no piping).
      # --force bypasses approvals (headless autonomy).
      "$SCRIPT_DIR/scripts/exec-clean.sh" --cd "$SCRIPT_DIR" "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CURSOR_BIN $CURSOR_MODEL_FLAG --force "$AGENT_PROMPT" || true
    else
      # Non-interactive: -p/--print with plain-text output. --force bypasses approvals,
      # --trust trusts the workspace (headless only). cursor-agent has no --output-last-message,
      # so the completion check greps the full captured output (see below).
      "$SCRIPT_DIR/scripts/exec-clean.sh" --cd "$SCRIPT_DIR" "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CURSOR_BIN -p --output-format text $CURSOR_MODEL_FLAG --force --trust "$AGENT_PROMPT" </dev/null 2>&1 \
        | "$SCRIPT_DIR/scripts/exec-clean.sh" tee -a "$ITERATION_LOG" > "$TEMP_OUTPUT" || true
    fi
  else
    CLAUDE_MODEL_FLAG=""
    if [ -n "$BOT_CLAUDE_MODEL" ]; then
      CLAUDE_MODEL_FLAG="--model $BOT_CLAUDE_MODEL"
    fi
    if [ "$USE_TUI" = true ]; then
      # TUI mode: let Claude own the terminal directly (no piping)
      "$SCRIPT_DIR/scripts/exec-clean.sh" --cd "$SCRIPT_DIR" "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CLAUDE_BIN $CLAUDE_MODEL_FLAG --dangerously-skip-permissions --session-id "$SESSION_ID" "$AGENT_PROMPT" || true
    else
      "$SCRIPT_DIR/scripts/exec-clean.sh" --cd "$SCRIPT_DIR" "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CLAUDE_BIN $CLAUDE_MODEL_FLAG --dangerously-skip-permissions --print --verbose --output-format stream-json --session-id "$SESSION_ID" "$AGENT_PROMPT" </dev/null 2>&1 \
        | "$SCRIPT_DIR/scripts/exec-clean.sh" tee -a "$ITERATION_LOG" > "$TEMP_OUTPUT" || true
    fi
  fi

  stop_title_watch

  if [ "$BOT_AGENT" = "claude" ]; then
    echo "To continue this session: claude --resume $SESSION_ID"
  elif [ "$BOT_AGENT" = "cursor" ]; then
    echo "To continue this session: cursor-agent resume"
  elif [ "$BOT_AGENT" = "bravebot" ]; then
    echo "To continue this session: bravebot --continue (in $BRAVE_ROOT)"
  else
    echo "To continue this session: codex resume --last"
  fi

  # Where the story actually landed. The agent moved it (or did not) via
  # update-prd-status.py, so the PRD is the only honest answer -- and the next
  # step follows from the new status, not the one this iteration started on.
  END_STATUS=$(jq -r --arg id "$STORY_ID" 'first(.stories[] | select(.id == $id)) | .status // empty' "$PRD_FILE" 2>/dev/null || echo "")
  END_PR_URL=$(jq -r --arg id "$STORY_ID" 'first(.stories[] | select(.id == $id)) | .prUrl // empty' "$PRD_FILE" 2>/dev/null || echo "")
  END_PR_NUMBER=$(jq -r --arg id "$STORY_ID" 'first(.stories[] | select(.id == $id)) | .prNumber // empty' "$PRD_FILE" 2>/dev/null || echo "")
  echo ""
  if [ -z "$END_STATUS" ]; then
    echo "$STORY_ID: could not read status back from $PRD_FILE."
  elif [ "$END_STATUS" != "$STORY_STATUS" ]; then
    echo "$STORY_ID: $STORY_STATUS -> $END_STATUS. Next: $(story_next_step "$END_STATUS")"
  else
    echo "$STORY_ID: still $STORY_STATUS. Next: $(story_next_step "$END_STATUS")"
  fi
  if [ -n "$END_PR_URL" ] && [ "$END_PR_URL" != "$STORY_PR_URL" ]; then
    echo "$STORY_ID: PR $END_PR_URL"
  fi
  # The watcher polls, so it can be stopped between the PR landing and its next
  # look. This is the same title, from the number the PRD just gave back.
  bot_set_terminal_title \
    "$(bot_story_title "$STORY_ISSUE" "$END_PR_NUMBER" "$STORY_ID" "$STORY_TITLE")"

  # --- Comparison run: steps 2 and 3 of this same turn (--comparison-run only) ---
  #
  # Step 1 was the base run above. Step 2 works the same story from scratch with
  # another tool, in a worktree of its own, and hands back its session id. Step 3
  # reads both sessions and files an issue for each gap the base tool hit and the
  # other one did not.
  #
  # Nothing in here may end the iteration. run.sh is `set -e`, and a comparison
  # that fails is a missing second opinion, not a failed story — so every call is
  # `|| true` with a warning. The story claim stays held throughout: this is
  # still the same iteration, and the heartbeat says the slot is alive across
  # what can be two more hours of agent time.
  if [ "$COMPARISON_RUN" = true ]; then
    bot_slot_heartbeat
    COMPARISON_BRANCH="$CLI_COMPARISON_BRANCH"
    if [ -z "$COMPARISON_BRANCH" ]; then
      COMPARISON_BRANCH="comparison-$(echo "$STORY_ID" | tr '[:upper:]' '[:lower:]')-$(date +%s)"
    fi
    COMPARISON_LOG="$LOGS_DIR/comparison-${RUN_ID_SAFE}-slot-${BOT_RUN_SLOT}-loop-${loop_count}.log"
    EVALUATOR_LOG="$LOGS_DIR/evaluator-${RUN_ID_SAFE}-slot-${BOT_RUN_SLOT}-loop-${loop_count}.log"
    COMPARISON_PROMPT_FILE=$(mktemp "${TMPDIR:-/tmp}/comparison-base-prompt.XXXXXX")
    BASE_SESSION_FILE=$(mktemp "${TMPDIR:-/tmp}/base-session.XXXXXX")
    COMPARISON_SESSION_FILE=$(mktemp "${TMPDIR:-/tmp}/comparison-session.XXXXXX")
    # The prompt goes by file, not argv: it carries the story JSON and the whole
    # bot config.
    printf '%s\n' "$AGENT_PROMPT" > "$COMPARISON_PROMPT_FILE"

    # What the base run left behind, for the evaluator to read. The branch comes
    # from the PRD (the agent may have set it this iteration) and its worktree
    # from git, rather than being guessed from a naming convention.
    END_BRANCH=$(jq -r --arg id "$STORY_ID" 'first(.stories[] | select(.id == $id)) | .branchName // empty' "$PRD_FILE" 2>/dev/null || echo "")
    BASE_WORKTREE=""
    if [ -n "$END_BRANCH" ]; then
      BASE_WORKTREE=$(git -C "$GIT_REPO" worktree list --porcelain 2>/dev/null \
        | awk -v b="branch refs/heads/$END_BRANCH" '/^worktree /{w=substr($0,10)} $0==b{print w; exit}')
    fi
    # A TUI iteration writes no log at all, so name one only when it exists:
    # a path to a missing file reads to the evaluator like something went wrong.
    BASE_LOG=""
    if [ -f "$ITERATION_LOG" ]; then
      BASE_LOG="$ITERATION_LOG"
    fi
    # Every launch above ran with --cd "$SCRIPT_DIR", so that is the directory
    # the agent's session was recorded against.
    python3 "$SCRIPT_DIR/scripts/find-agent-session.py" \
      --agent "$BOT_AGENT" --cwd "$SCRIPT_DIR" --since "$BASE_STARTED_AT" \
      --session-id "$SESSION_ID" > "$BASE_SESSION_FILE" 2>/dev/null || true
    if [ ! -s "$BASE_SESSION_FILE" ]; then
      echo '{}' > "$BASE_SESSION_FILE"
    fi
    TMP_BASE_SESSION=$(mktemp "${TMPDIR:-/tmp}/base-session-enriched.XXXXXX")
    jq --arg agent "$BOT_AGENT" --arg branch "$END_BRANCH" --arg worktree "$BASE_WORKTREE" \
       --arg log "$BASE_LOG" --arg tui "$USE_TUI" \
       --arg startStatus "$STORY_STATUS" --arg endStatus "$END_STATUS" --arg prUrl "$END_PR_URL" \
      '. + {agent: $agent, branch: $branch, worktree: $worktree, log: $log,
            tui: ($tui == "true"), startStatus: $startStatus, endStatus: $endStatus, prUrl: $prUrl}' \
      "$BASE_SESSION_FILE" > "$TMP_BASE_SESSION" 2>/dev/null \
      && mv "$TMP_BASE_SESSION" "$BASE_SESSION_FILE"
    rm -f "$TMP_BASE_SESSION"
    BASE_SESSION_ID=$(jq -r '.sessionId // empty' "$BASE_SESSION_FILE" 2>/dev/null || echo "")

    echo ""
    echo "==============================================================="
    echo "  Comparison run (step 2 of 3) — $COMPARISON_AGENT, branch $COMPARISON_BRANCH"
    echo "  Base:      $BOT_AGENT session ${BASE_SESSION_ID:-not identified}"
    echo "  Log:       $COMPARISON_LOG"
    echo "==============================================================="
    COMPARISON_RC=0
    "$SCRIPT_DIR/scripts/comparison-run.sh" \
      --story-id "$STORY_ID" --status "$STORY_STATUS" \
      --branch "$COMPARISON_BRANCH" --prompt-file "$COMPARISON_PROMPT_FILE" \
      --log "$COMPARISON_LOG" --session-out "$COMPARISON_SESSION_FILE" \
      --agent "$COMPARISON_AGENT" --agent-bin "$COMPARISON_AGENT_BIN" \
      --model "$COMPARISON_MODEL" \
      --base-agent "$BOT_AGENT" --base-session-id "$BASE_SESSION_ID" || COMPARISON_RC=$?
    bot_slot_heartbeat

    if [ "$COMPARISON_RC" -ne 0 ]; then
      echo ""
      echo "  Comparison run failed (exit $COMPARISON_RC) — nothing to evaluate against." >&2
      echo "  See $COMPARISON_LOG" >&2
    else
      echo ""
      echo "==============================================================="
      echo "  Evaluator run (step 3 of 3) — $COMPARISON_AGENT critiques the base run"
      echo "  Base:       $BOT_AGENT session ${BASE_SESSION_ID:-not identified}"
      echo "  Comparison: $COMPARISON_AGENT session $(jq -r '.sessionId // "not identified"' "$COMPARISON_SESSION_FILE" 2>/dev/null || echo "not identified")"
      echo "  Issues to:  $BOT_ISSUE_REPO"
      echo "  Log:        $EVALUATOR_LOG"
      echo "==============================================================="
      "$SCRIPT_DIR/scripts/comparison-evaluate.sh" \
        --story-id "$STORY_ID" --status "$STORY_STATUS" --story-title "$STORY_TITLE" \
        --base-session "$BASE_SESSION_FILE" --comparison-session "$COMPARISON_SESSION_FILE" \
        --prompt-file "$COMPARISON_PROMPT_FILE" --log "$EVALUATOR_LOG" \
        --agent "$COMPARISON_AGENT" --agent-bin "$COMPARISON_AGENT_BIN" \
        --model "$COMPARISON_MODEL" \
        --run-id "$RUN_ID" --slot "$BOT_RUN_SLOT" --loop "$loop_count" \
        || echo "  Evaluator run failed — see $EVALUATOR_LOG" >&2
      bot_slot_heartbeat
    fi
    rm -f "$COMPARISON_PROMPT_FILE" "$BASE_SESSION_FILE" "$COMPARISON_SESSION_FILE"
  fi

  # Check for completion signal (print mode only — TUI mode skips this since user is watching).
  # Match ONLY the agent's own final message, never raw tool/file output: the marker is
  # documented verbatim in .claude/CLAUDE.md and docs/WORKFLOW.md, so grepping the full
  # stream would false-positive the moment the agent reads one of those files.
  COMPLETION_CHECK=0
  if [ "$USE_TUI" != true ]; then
    if [ "$BOT_AGENT" = "codex" ]; then
      # Codex's --output-last-message file holds just the final agent message.
      COMPLETION_CHECK=$(grep -c -F "<promise>COMPLETE</promise>" "$TEMP_LAST_MSG" 2>/dev/null | tail -1)
    elif [ "$BOT_AGENT" = "cursor" ]; then
      # cursor-agent has no --output-last-message; -p --output-format text prints the
      # assistant's response text to stdout, captured in TEMP_OUTPUT. Grep that.
      # (A logged-in maintainer can switch to --output-format stream-json parsing
      # once its event schema is known, to avoid false positives from echoed doc text.)
      COMPLETION_CHECK=$(grep -c -F "<promise>COMPLETE</promise>" "$TEMP_OUTPUT" 2>/dev/null | tail -1)
    elif [ "$BOT_AGENT" = "bravebot" ]; then
      # bravebot's stdout is the final agent message, captured on its own above.
      COMPLETION_CHECK=$(grep -c -F "<promise>COMPLETE</promise>" "$TEMP_LAST_MSG" 2>/dev/null | tail -1)
    else
      # Claude stream-json: extract assistant text only, excluding tool_result events.
      COMPLETION_CHECK=$(jq -r 'select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text' "$TEMP_OUTPUT" 2>/dev/null | grep -c -F "<promise>COMPLETE</promise>" 2>/dev/null | tail -1)
    fi
    COMPLETION_CHECK=$((COMPLETION_CHECK + 0))
  fi
  if [ "$COMPLETION_CHECK" -gt 0 ]; then
    echo ""
    echo "Agent completed all tasks!"
    echo "Completed at work iteration $work_iteration (loop $loop_count of $MAX_ITERATIONS)"
    rm -f "$TEMP_OUTPUT" "$TEMP_LAST_MSG"
    exit 0
  fi

  rm -f "$TEMP_OUTPUT" "$TEMP_LAST_MSG"

  # The iteration is over: let another run pick this story up.
  release_claim
  bot_slot_heartbeat

  echo "Loop $loop_count complete. Starting fresh context..."
  sleep 2
done

echo ""
echo "Agent reached max loop iterations ($MAX_ITERATIONS) without completing all tasks."
echo "Work iterations completed: $work_iteration"
echo "Check $PROGRESS_FILE for status."
exit 1
