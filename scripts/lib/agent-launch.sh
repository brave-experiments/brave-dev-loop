#!/bin/bash
# Launch an agent CLI non-interactively, for the runs that are not the base run.
#
# run.sh keeps its own launcher: it also has to offer TUI mode, hand Claude the
# session id it pre-generated, and capture the final message for the completion
# check. This one is the plain case — a prompt in, a log and a final message out
# — and it is shared by the comparison run and the evaluator run so the two
# cannot drift apart in how they invoke a tool.
#
# Usage:
#   source scripts/lib/agent-launch.sh
#   bot_launch_agent <agent> <bin> <model> <cwd> <log> <prompt-file> <final-msg-file> <timeout>
#
# Sets BOT_LAUNCH_SESSION_ID (Claude only — the one agent that is told its id;
# every other agent's session is found afterwards by find-agent-session.py) and
# BOT_LAUNCH_STARTED_AT, the epoch second before the agent started, which is
# what that search narrows on.
#
# Returns the agent's exit status. A failed agent is the caller's business to
# report; it is never fatal here.

BOT_AGENT_LAUNCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bot_launch_agent() {
  local agent="$1" bin="$2" model="$3" cwd="$4" log="$5" prompt_file="$6"
  local final_msg="$7" timeout="${8:-7200}"

  local prompt
  prompt=$(cat "$prompt_file")

  BOT_LAUNCH_SESSION_ID=""
  BOT_LAUNCH_STARTED_AT=$(date +%s)

  # Every launch goes through exec-clean.sh: fd 200 carries this run's slot
  # lock, and a child that inherits it holds the slot after the run is gone.
  local clean="$BOT_AGENT_LAUNCH_DIR/exec-clean.sh"
  local timeout_tree="$BOT_AGENT_LAUNCH_DIR/timeout-tree.sh"
  local rc=0

  case "$agent" in
    claude)
      local model_flag=""
      [ -n "$model" ] && model_flag="--model $model"
      BOT_LAUNCH_SESSION_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
      "$clean" --cd "$cwd" "$timeout_tree" "$timeout" \
        $bin $model_flag --dangerously-skip-permissions --print --verbose \
        --output-format stream-json --session-id "$BOT_LAUNCH_SESSION_ID" "$prompt" \
        </dev/null 2>&1 | "$clean" tee -a "$log" >/dev/null || rc=$?
      # The final assistant text, never a tool result: the same extraction run.sh
      # uses for its completion check.
      jq -r 'select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text' \
        "$log" 2>/dev/null | tail -40 > "$final_msg" || true
      ;;
    bravebot)
      local model_flag=""
      [ -n "$model" ] && model_flag="--model $model"
      # bravebot keeps stdout to the final reply and puts progress on stderr,
      # quarantined-content previews included, so stderr goes straight to the log.
      "$clean" --cd "$cwd" "$timeout_tree" "$timeout" \
        $bin $model_flag --dangerously-skip-permissions "$prompt" \
        </dev/null 2>>"$log" \
        | "$clean" tee -a "$log" > "$final_msg" || rc=$?
      ;;
    codex)
      local model_flag=""
      [ -n "$model" ] && model_flag="--model $model"
      "$clean" --cd "$cwd" "$timeout_tree" "$timeout" \
        $bin exec $model_flag --dangerously-bypass-approvals-and-sandbox --json \
        --skip-git-repo-check --output-last-message "$final_msg" "$prompt" \
        </dev/null 2>&1 | "$clean" tee -a "$log" >/dev/null || rc=$?
      ;;
    cursor)
      local model_flag=""
      [ -n "$model" ] && model_flag="--model $model"
      # cursor-agent has no --output-last-message; -p prints the reply to stdout.
      "$clean" --cd "$cwd" "$timeout_tree" "$timeout" \
        $bin -p --output-format text $model_flag --force --trust "$prompt" \
        </dev/null 2>>"$log" \
        | "$clean" tee -a "$log" > "$final_msg" || rc=$?
      ;;
    *)
      echo "Unsupported agent '$agent' (expected: claude | codex | cursor | bravebot)" >&2
      return 2
      ;;
  esac

  return $rc
}

# The session the launch above wrote, as JSON, or nothing when it cannot be
# found. `exclude` keeps a base and comparison run that used the same tool in
# the same directory from resolving to the same session.
bot_find_launched_session() {
  local agent="$1" cwd="$2" since="$3" session_id="$4" exclude="$5"
  python3 "$BOT_AGENT_LAUNCH_DIR/find-agent-session.py" \
    --agent "$agent" --cwd "$cwd" --since "$since" \
    --session-id "$session_id" --exclude "$exclude" 2>/dev/null || true
}
