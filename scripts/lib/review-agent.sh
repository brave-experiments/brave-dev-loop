#!/bin/bash
# The command line for an unattended review-side agent session: a /review-prs
# sweep (review-session.sh) or a ping reply (reply-to-pings.sh), run by whichever
# agent reviewer.agent in config.json names (BOT_REVIEW_AGENT).
#
#   source scripts/lib/load-config.sh
#   source scripts/lib/review-agent.sh
#   bot_review_agent_cmd review|reply <prompt> <model> <settings-json> <work-dir> [<read-only-dir>...]
#   "${BOT_REVIEW_AGENT_CMD[@]}"
#
# The first directory is the run's work directory, the one place the session may
# write; the rest are readable only. The caller still runs the command with no
# GitHub token and an empty gh config directory.
#
# claude is held by an allowlist (review-session-settings.py's rules under
# --permission-mode dontAsk): anything the rules do not allow is refused.
#
# bravebot cannot be held that way. A one-shot run has nobody to ask, so a write or
# a command it is not allowed to make ends the run on status 4, and an `allow` rule
# decides nothing there; the run would never reach its results file. The only mode
# in which the work gets done is --dangerously-skip-permissions, which answers every
# question yes and also vouches for every file, so what the session reads is no
# longer quarantined. What is left holding it, and what is not, is in
# docs/review-agents.md.

# The session's tools, by bravebot's names: nothing that fetches.
BOT_BRAVEBOT_REVIEW_TOOLS="read_file,list_files,search,repo_map,read_git,write_file,edit_file,run,spawn_agent,spawn_processor,load_skill,search_skills"
# A reply session reads a thread and writes one file: no program, no delegate.
BOT_BRAVEBOT_REPLY_TOOLS="read_file,list_files,search,write_file,edit_file,load_skill,search_skills"

# Sets BOT_REVIEW_AGENT_CMD. Returns 2 for an agent that cannot review.
#
# `settings` is the JSON review-session-settings.py printed. claude takes it inline;
# bravebot takes a path, so it is written to a file here and the file's name is left
# in BOT_REVIEW_AGENT_TMP for the caller to delete when the session is over.
bot_review_agent_cmd() {
  local kind="$1" prompt="$2" model="$3" settings="$4"
  shift 4
  local work_dir="${1:-}" dir tools path settings_file model_flag
  BOT_REVIEW_AGENT_CMD=()
  BOT_REVIEW_AGENT_TMP=""

  case "${BOT_REVIEW_AGENT:-claude}" in
    claude)
      BOT_REVIEW_AGENT_CMD=("$BOT_CLAUDE_BIN" -p "$prompt" --model "$model"
        --permission-mode dontAsk --settings "$settings")
      for dir in "$@"; do
        BOT_REVIEW_AGENT_CMD+=(--add-dir "$dir")
      done
      BOT_REVIEW_AGENT_CMD+=(--strict-mcp-config)
      ;;
    bravebot)
      tools="$BOT_BRAVEBOT_REVIEW_TOOLS"
      [ "$kind" = "reply" ] && tools="$BOT_BRAVEBOT_REPLY_TOOLS"
      # The prompt goes first: bravebot reads --model only after it.
      # --trust-workspace is what lets /review-prs, a skill under the bot
      # directory's .claude/skills, load at all.
      settings_file=$(mktemp "${TMPDIR:-/tmp}/review-settings.XXXXXX") || return 1
      # Bypass mode ignores the Edit allow rule that confines claude's writes to the
      # work directory, and deny rules still hold. So the confinement is written the
      # other way round: deny writes to the bot directory and to every directory
      # that is only there to be read.
      printf '%s\n' "$settings" | jq --arg bot "$BOT_DIR" --args '
        .permissions.deny += ([$bot] + $ARGS.positional | map("Edit(//" + (. | ltrimstr("/")) + "/**)"))
      ' "${@:2}" > "$settings_file" || { rm -f "$settings_file"; return 1; }
      BOT_REVIEW_AGENT_TMP="$settings_file"
      # $model is the reviewer's claude alias ("sonnet" by default), which bravebot
      # resolves to one of Brave's models and then refuses (BB1003) when the
      # operator has a service of their own. So it is not passed: the model is
      # bot.bravebotModel when set, else bravebot's own `model` setting.
      model_flag=()
      [ -n "${BOT_BRAVEBOT_MODEL:-}" ] && model_flag=(--model "$BOT_BRAVEBOT_MODEL")
      BOT_REVIEW_AGENT_CMD=("$BOT_BRAVEBOT_BIN" "$prompt" ${model_flag[@]+"${model_flag[@]}"}
        --settings "$settings_file" --trust-workspace --dangerously-skip-permissions
        --tools "$tools" --run-network closed
        --sandbox workspace-write --sandbox-deny-write "$BOT_DIR")
      for dir in "$@"; do
        BOT_REVIEW_AGENT_CMD+=(--add-dir "$dir")
      done
      [ -n "$work_dir" ] && BOT_REVIEW_AGENT_CMD+=(--sandbox-allow-write "$work_dir")
      # What a program the session starts must not read. The settings file's Read
      # rules say the same to the file tools; these say it to `run`.
      for path in "$HOME/.ssh" "$HOME/.config/gh" "$BOT_DIR/.envrc" \
          "${BOT_GH_CONFIG_DIR:-}" "${BOT_REVIEWER_GH_CONFIG_DIR:-}" \
          "${GH_CONFIG_DIR:-}" "${BOT_SSH_KEY_PATH:-}"; do
        [ -n "$path" ] && [ -e "$path" ] && BOT_REVIEW_AGENT_CMD+=(--sandbox-deny-read "$path")
      done
      ;;
    *)
      echo "Error: reviewer.agent '${BOT_REVIEW_AGENT}' cannot review (expected: claude | bravebot)." >&2
      return 2
      ;;
  esac
}
