#!/bin/bash
# Long-running AI agent loop
# Usage: ./run.sh [max_iterations] [tui] [--agent claude|codex|cursor] [--model model] [extra_prompt_info...]
#
# Agent selection precedence (highest wins):
#   1. --agent <name> CLI flag
#   2. BOT_AGENT env var
#   3. bot.agent in config.json
#   4. "claude" default
#
# Model selection:
#   --model <name> overrides bot.claudeModel, bot.codexModel, or bot.cursorModel for the selected agent.

set -e

# Prevent concurrent runs — acquire an exclusive lock or exit immediately
LOCKFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.run.lock"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/lib/lock.sh"
bot_acquire_lock "$LOCKFILE"
case $? in
  0) ;;
  1) echo "Another run.sh is already running. Exiting."; exit 0 ;;
  *) echo "Could not acquire the run lock. Exiting." >&2; exit 1 ;;
esac

# Parse arguments
MAX_ITERATIONS=10

USE_TUI=false
EXTRA_PROMPT=""
PAST_TUI=false
CLI_AGENT=""
CLI_MODEL=""
EXPECT_AGENT_VALUE=false
EXPECT_MODEL_VALUE=false

for arg in "$@"; do
  if [ "$EXPECT_AGENT_VALUE" = true ]; then
    CLI_AGENT="$arg"
    EXPECT_AGENT_VALUE=false
    continue
  fi
  if [ "$EXPECT_MODEL_VALUE" = true ]; then
    CLI_MODEL="$arg"
    EXPECT_MODEL_VALUE=false
    continue
  fi
  # --agent is recognized regardless of position (before or after `tui`).
  if [[ "$arg" == "--agent" ]]; then
    EXPECT_AGENT_VALUE=true
    continue
  elif [[ "$arg" == --agent=* ]]; then
    CLI_AGENT="${arg#--agent=}"
    continue
  elif [[ "$arg" == "--model" ]]; then
    EXPECT_MODEL_VALUE=true
    continue
  elif [[ "$arg" == --model=* ]]; then
    CLI_MODEL="${arg#--model=}"
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
  echo "Error: --agent requires a value (expected: claude | codex | cursor)" >&2
  exit 1
fi
if [ "$EXPECT_MODEL_VALUE" = true ]; then
  echo "Error: --model requires a value" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/scripts/lib/load-config.sh"
source "$SCRIPT_DIR/scripts/lib/git-identity.sh"

# Pin this run to the bot's GitHub identity before anything can touch GitHub.
bot_export_identity_env "$BOT_SSH_KEY_PATH" "$BOT_GH_ACCOUNT" || exit 1

# CLI --agent flag has the final say (overrides env + config)
if [ -n "$CLI_AGENT" ]; then
  BOT_AGENT="$CLI_AGENT"
fi
case "$BOT_AGENT" in
  claude|codex|cursor) ;;
  *)
    echo "Error: unsupported agent '$BOT_AGENT' (expected: claude | codex | cursor)" >&2
    exit 1
    ;;
esac
if [ -n "$CLI_MODEL" ]; then
  if [ "$BOT_AGENT" = "codex" ]; then
    BOT_CODEX_MODEL="$CLI_MODEL"
  elif [ "$BOT_AGENT" = "cursor" ]; then
    BOT_CURSOR_MODEL="$CLI_MODEL"
  else
    BOT_CLAUDE_MODEL="$CLI_MODEL"
  fi
fi

PRD_FILE="$SCRIPT_DIR/data/prd.json"
PROGRESS_FILE="$SCRIPT_DIR/data/progress.txt"
LOGS_DIR="$SCRIPT_DIR/logs"
RUN_STATE_FILE="$SCRIPT_DIR/data/run-state.json"

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

# Function to switch back to master branch on exit
cleanup_and_return_to_master() {
  bot_release_lock
  if [ -n "$GIT_REPO" ] && [ -d "$GIT_REPO/.git" ]; then
    echo ""
    echo "Switching back to $BOT_DEFAULT_BRANCH branch in $GIT_REPO..."
    cd "$GIT_REPO"
    git stash --include-untracked 2>/dev/null || true
    git checkout "$BOT_DEFAULT_BRANCH" 2>/dev/null || echo "Could not switch to $BOT_DEFAULT_BRANCH branch"
  fi
}

# Register cleanup function to run on exit
trap cleanup_and_return_to_master EXIT INT TERM HUP

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

if [ "$BOT_AGENT" = "codex" ]; then
  echo "Starting Codex agent - Max iterations: $MAX_ITERATIONS"
elif [ "$BOT_AGENT" = "cursor" ]; then
  echo "Starting Cursor agent - Max iterations: $MAX_ITERATIONS"
else
  echo "Starting Claude Code agent - Max iterations: $MAX_ITERATIONS"
fi
echo "Logs will be saved to: $LOGS_DIR"

# Fetch nightly version once per run (avoids redundant WebFetch in each iteration)
NIGHTLY_VERSION=$(python3 "$SCRIPT_DIR/scripts/get-nightly-version.py" 2>/dev/null || echo "")
if [ -n "$NIGHTLY_VERSION" ]; then
  echo "Nightly version: $NIGHTLY_VERSION"
else
  echo "Warning: Could not fetch nightly version (agent will fetch if needed)"
fi

# Reset run state at the start of each run
echo "Resetting run state for fresh start..."
"$SCRIPT_DIR/scripts/reset-run-state.sh"

# In auto mode the PRD is a cache — rebuild it from GitHub before selecting a
# task. Plain Python against the API; no agent is started, so this costs
# nothing. A curated PRD is authored, so a run leaves it alone entirely; its
# backlog top-up belongs to the scheduled job, at a time the operator picked.
if [ "$BOT_PRD_MODE" = "auto" ]; then
  "$SCRIPT_DIR/scripts/sync-prd.sh"
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
    TMP_RUN_STATE=$(mktemp)
    jq --arg runId "$RUN_ID" '.runId = $runId | .storiesCheckedThisRun = [] | .lastIterationHadStateChange = true' "$RUN_STATE_FILE" > "$TMP_RUN_STATE" && mv "$TMP_RUN_STATE" "$RUN_STATE_FILE"
  fi

  # Generate log file path for this iteration (needed by select-task.py)
  RUN_ID_SAFE=$(echo "$RUN_ID" | sed 's/[^a-zA-Z0-9-]/-/g')
  ITERATION_LOG="$LOGS_DIR/iteration-${RUN_ID_SAFE}-loop-${loop_count}.log"

  # Select next task — this is the gate check; exit early if no candidates
  TASK_JSON=""
  if [ -n "$EXTRA_PROMPT" ]; then
    TASK_JSON=$(python3 "$SCRIPT_DIR/scripts/select-task.py" \
      --prd "$PRD_FILE" \
      --run-state "$RUN_STATE_FILE" \
      --iteration-log "$ITERATION_LOG" \
      --claude-bin "$BOT_CLAUDE_BIN" \
      --extra-prompt "$EXTRA_PROMPT") || true
  else
    TASK_JSON=$(python3 "$SCRIPT_DIR/scripts/select-task.py" \
      --prd "$PRD_FILE" \
      --run-state "$RUN_STATE_FILE" \
      --iteration-log "$ITERATION_LOG") || true
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
  echo "Selected: $STORY_ID - $STORY_TITLE (status: $STORY_STATUS, tier: $TIER_NAME)"

  # Task confirmed — proceed with iteration setup
  # Store the current iteration log path in run-state.json
  TMP_RUN_STATE=$(mktemp)
  jq --arg logPath "$ITERATION_LOG" '.currentIterationLogPath = $logPath' "$RUN_STATE_FILE" > "$TMP_RUN_STATE" && mv "$TMP_RUN_STATE" "$RUN_STATE_FILE"

  # Check if last iteration had state change (default to true for first iteration)
  HAD_STATE_CHANGE=$(jq -r '.lastIterationHadStateChange // true' "$RUN_STATE_FILE" 2>/dev/null || echo "true")

  # Only increment work iteration counter if there was actual state change
  if [ "$HAD_STATE_CHANGE" = "true" ]; then
    ((++work_iteration))
    echo ""
    echo "==============================================================="
    echo "  Work Iteration $work_iteration (loop $loop_count of $MAX_ITERATIONS)"
    echo "==============================================================="
  else
    echo ""
    echo "==============================================================="
    echo "  Checking next task (work iteration $work_iteration, loop $loop_count of $MAX_ITERATIONS)"
    echo "  Previous check had no state change - continuing without incrementing work iteration"
    echo "==============================================================="
  fi

  echo "Logging to: $ITERATION_LOG"

  # Run Claude Code with the agent prompt
  # Use a temp file to capture output while allowing real-time streaming
  TEMP_OUTPUT=$(mktemp)
  # Codex writes only its final agent message here (used for completion detection,
  # so file contents read mid-iteration can never trip the <promise>COMPLETE</promise> check).
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
  # Absent project.profile means a deployment predating profiles — all brave-core.
  BOT_PROFILE=$(bot_config '.project.profile')
  BOT_PROFILE="${BOT_PROFILE:-brave-core}"
  AGENT_PROMPT="You are working on story $STORY_ID (current status: $STORY_STATUS).
Follow ./$BOT_DIRNAME/docs/workflow-${STORY_STATUS}.md for the workflow.
Follow the general instructions in ./$BOT_DIRNAME/.claude/CLAUDE.md.

Project-specific rules live in ./$BOT_DIRNAME/projects/$BOT_PROFILE/docs/.
Where a workflow doc says a step is project-specific, read the named file there.

Story details:
$STORY_DETAILS

Bot config (from config.json — do NOT read this file):
$BOT_CONFIG"
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

  # Run the agent from the bot directory so it picks up project instructions.
  if [ "$BOT_AGENT" = "codex" ]; then
    CODEX_MODEL_FLAG=""
    if [ -n "$BOT_CODEX_MODEL" ]; then
      CODEX_MODEL_FLAG="--model $BOT_CODEX_MODEL"
    fi
    if [ "$USE_TUI" = true ]; then
      # TUI mode: let codex own the terminal directly (no piping).
      (cd "$SCRIPT_DIR" && "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CODEX_BIN $CODEX_MODEL_FLAG --dangerously-bypass-approvals-and-sandbox "$AGENT_PROMPT") 200>&- || true
    else
      # Non-interactive: stream JSONL events to the iteration log; capture the
      # final agent message separately for the completion check.
      (cd "$SCRIPT_DIR" && "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CODEX_BIN exec $CODEX_MODEL_FLAG --dangerously-bypass-approvals-and-sandbox --json --skip-git-repo-check --output-last-message "$TEMP_LAST_MSG" "$AGENT_PROMPT") 200>&- </dev/null 2>&1 | tee -a "$ITERATION_LOG" > "$TEMP_OUTPUT" || true
    fi
  elif [ "$BOT_AGENT" = "cursor" ]; then
    CURSOR_MODEL_FLAG=""
    if [ -n "$BOT_CURSOR_MODEL" ]; then
      CURSOR_MODEL_FLAG="--model $BOT_CURSOR_MODEL"
    fi
    if [ "$USE_TUI" = true ]; then
      # TUI mode: let cursor-agent own the terminal directly (no piping).
      # --force bypasses approvals (headless autonomy).
      (cd "$SCRIPT_DIR" && "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CURSOR_BIN $CURSOR_MODEL_FLAG --force "$AGENT_PROMPT") 200>&- || true
    else
      # Non-interactive: -p/--print with plain-text output. --force bypasses approvals,
      # --trust trusts the workspace (headless only). cursor-agent has no --output-last-message,
      # so the completion check greps the full captured output (see below).
      (cd "$SCRIPT_DIR" && "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CURSOR_BIN -p --output-format text $CURSOR_MODEL_FLAG --force --trust "$AGENT_PROMPT") 200>&- </dev/null 2>&1 | tee -a "$ITERATION_LOG" > "$TEMP_OUTPUT" || true
    fi
  else
    CLAUDE_MODEL_FLAG=""
    if [ -n "$BOT_CLAUDE_MODEL" ]; then
      CLAUDE_MODEL_FLAG="--model $BOT_CLAUDE_MODEL"
    fi
    if [ "$USE_TUI" = true ]; then
      # TUI mode: let Claude own the terminal directly (no piping)
      (cd "$SCRIPT_DIR" && "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CLAUDE_BIN $CLAUDE_MODEL_FLAG --dangerously-skip-permissions --session-id "$SESSION_ID" "$AGENT_PROMPT") 200>&- || true
    else
      (cd "$SCRIPT_DIR" && "$SCRIPT_DIR/scripts/timeout-tree.sh" 7200 $BOT_CLAUDE_BIN $CLAUDE_MODEL_FLAG --dangerously-skip-permissions --print --verbose --output-format stream-json --session-id "$SESSION_ID" "$AGENT_PROMPT") 200>&- </dev/null 2>&1 | tee -a "$ITERATION_LOG" > "$TEMP_OUTPUT" || true
    fi
  fi

  if [ "$BOT_AGENT" = "claude" ]; then
    echo "To continue this session: claude --resume $SESSION_ID"
  elif [ "$BOT_AGENT" = "cursor" ]; then
    echo "To continue this session: cursor-agent resume"
  else
    echo "To continue this session: codex resume --last"
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

  echo "Loop $loop_count complete. Starting fresh context..."
  sleep 2
done

echo ""
echo "Agent reached max loop iterations ($MAX_ITERATIONS) without completing all tasks."
echo "Work iterations completed: $work_iteration"
echo "Check $PROGRESS_FILE for status."
exit 1
