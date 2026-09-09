#!/bin/bash
# Shared config loader for brave-dev-bot shell scripts.
#
# Usage (from any script):
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/load-config.sh"
#
# Exports:
#   BOT_PROJECT_NAME, BOT_ORG, BOT_PR_REPO, BOT_ISSUE_REPO,
#   BOT_DEFAULT_BRANCH, BOT_USERNAME, BOT_EMAIL,
#   BOT_SSH_KEY_PATH, BOT_GH_ACCOUNT,
#   BOT_AGENT, BOT_CLAUDE_MODEL, BOT_CLAUDE_BIN,
#   BOT_CODEX_MODEL, BOT_CODEX_BIN,
#   BOT_CURSOR_MODEL, BOT_CURSOR_BIN, BOT_BP_DOCS_DIR,
#   BOT_TARGET_REPO_DIR (absolute; prefer over the raw BOT_TARGET_REPO_PATH)
#
# Also provides:
#   bot_config '.some.jq.path'  — raw jq query against the config file

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

BOT_CONFIG_FILE="$BOT_DIR/config.json"
if [ ! -f "$BOT_CONFIG_FILE" ]; then
  BOT_CONFIG_FILE="$BOT_DIR/config.example.json"
fi

if [ ! -f "$BOT_CONFIG_FILE" ]; then
  echo "Error: No config.json or config.example.json found in $BOT_DIR" >&2
  return 1 2>/dev/null || exit 1
fi

bot_config() {
  jq -r "$1 // empty" "$BOT_CONFIG_FILE"
}

# Resolve bestPractices.docsDir to an absolute directory. Stored relative to
# the bot dir, but the same base ambiguity that affects targetRepoPath has
# produced parent-relative values in the wild, so both bases are tried.
resolve_docs_dir() {
  local path="$1"
  [ -z "$path" ] && return 0

  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
    return 0
  fi

  local bot_base parent_base
  bot_base="$BOT_DIR/$path"
  parent_base="$(dirname "$BOT_DIR")/$path"

  if [ -d "$bot_base" ]; then
    (cd "$bot_base" && pwd)
  elif [ -d "$parent_base" ]; then
    (cd "$parent_base" && pwd)
  else
    printf '%s\n' "$bot_base"
  fi
}

# Resolve project.targetRepoPath to an absolute directory.
#
# The setup wizard documents this path as relative to the bot directory, but
# config.brave-core.json (and every deployment seeded from it) stores a value
# relative to the bot directory's *parent* — "src/brave", not "../src/brave".
# Both spellings are in the wild, so try both bases and use whichever one is
# actually a git repo. Absolute paths are used as-is.
resolve_target_repo() {
  local path="$1"
  [ -z "$path" ] && return 0

  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
    return 0
  fi

  local bot_base parent_base
  bot_base="$BOT_DIR/$path"
  parent_base="$(dirname "$BOT_DIR")/$path"

  if [ -e "$bot_base/.git" ]; then
    (cd "$bot_base" && pwd)
  elif [ -e "$parent_base/.git" ]; then
    (cd "$parent_base" && pwd)
  else
    # Neither exists — return the documented base so errors name a sane path.
    printf '%s\n' "$bot_base"
  fi
}

BOT_PROJECT_NAME=$(bot_config '.project.name')
BOT_ORG=$(bot_config '.project.org')
BOT_PR_REPO=$(bot_config '.project.prRepository')
BOT_ISSUE_REPO=$(bot_config '.project.issueRepository')
BOT_DEFAULT_BRANCH=$(bot_config '.project.defaultBranch')
BOT_TARGET_REPO_PATH=$(bot_config '.project.targetRepoPath')

BOT_USERNAME=$(bot_config '.bot.username')
BOT_EMAIL=$(bot_config '.bot.email')
BOT_SSH_KEY_PATH=$(bot_config '.bot.sshKeyPath')
BOT_GH_ACCOUNT=$(bot_config '.bot.ghAccount')
# BOT_AGENT env var (if set) takes precedence over config
_BOT_AGENT_ENV="${BOT_AGENT:-}"
BOT_AGENT=$(bot_config '.bot.agent')
if [ -n "$_BOT_AGENT_ENV" ]; then
  BOT_AGENT="$_BOT_AGENT_ENV"
fi
unset _BOT_AGENT_ENV
BOT_CLAUDE_MODEL=$(bot_config '.bot.claudeModel')
BOT_CLAUDE_BIN=$(bot_config '.bot.claudeBin')
BOT_CODEX_MODEL=$(bot_config '.bot.codexModel')
BOT_CODEX_BIN=$(bot_config '.bot.codexBin')
BOT_CURSOR_MODEL=$(bot_config '.bot.cursorModel')
BOT_CURSOR_BIN=$(bot_config '.bot.cursorBin')

BOT_BP_DOCS_DIR=$(bot_config '.bestPractices.docsDir')

# Absolute paths. Prefer these over the raw (base-ambiguous) config values.
BOT_TARGET_REPO_DIR=$(resolve_target_repo "$BOT_TARGET_REPO_PATH")
BOT_BP_DOCS_DIR_ABS=$(resolve_docs_dir "$BOT_BP_DOCS_DIR")

# Validate required fields
_missing=""
[ -z "$BOT_PROJECT_NAME" ] && _missing="$_missing project.name"
[ -z "$BOT_ORG" ] && _missing="$_missing project.org"
[ -z "$BOT_PR_REPO" ] && _missing="$_missing project.prRepository"
[ -z "$BOT_ISSUE_REPO" ] && _missing="$_missing project.issueRepository"
[ -z "$BOT_USERNAME" ] && _missing="$_missing bot.username"
if [ -n "$_missing" ]; then
  echo "Error: Missing required config values in $BOT_CONFIG_FILE:$_missing" >&2
  echo "  Run 'make setup' or edit config.json directly." >&2
  return 1 2>/dev/null || exit 1
fi

# Fall back to 'opus' if claudeModel is not set
if [ -z "$BOT_CLAUDE_MODEL" ]; then
  BOT_CLAUDE_MODEL="opus[1m]"
fi

# Fall back to 'claude' if claudeBin is not set
if [ -z "$BOT_CLAUDE_BIN" ]; then
  BOT_CLAUDE_BIN="$(which claude 2>/dev/null || echo "claude")"
fi

# Fall back to 'codex' if codexBin is not set
if [ -z "$BOT_CODEX_BIN" ]; then
  BOT_CODEX_BIN="$(which codex 2>/dev/null || echo "codex")"
fi

# Fall back to 'cursor-agent' if cursorBin is not set
if [ -z "$BOT_CURSOR_BIN" ]; then
  BOT_CURSOR_BIN="$(which cursor-agent 2>/dev/null || echo "cursor-agent")"
fi

# Default agent is 'claude' if not configured. BOT_AGENT env var overrides config.
BOT_AGENT="${BOT_AGENT:-claude}"

# The gh account defaults to the bot's GitHub username.
if [ -z "$BOT_GH_ACCOUNT" ]; then
  BOT_GH_ACCOUNT="$BOT_USERNAME"
fi

export BOT_DIR BOT_CONFIG_FILE
export BOT_PROJECT_NAME BOT_ORG BOT_PR_REPO BOT_ISSUE_REPO BOT_DEFAULT_BRANCH BOT_TARGET_REPO_PATH BOT_TARGET_REPO_DIR
export BOT_USERNAME BOT_EMAIL BOT_SSH_KEY_PATH BOT_GH_ACCOUNT
export BOT_AGENT BOT_CLAUDE_MODEL BOT_CLAUDE_BIN BOT_CODEX_MODEL BOT_CODEX_BIN BOT_CURSOR_MODEL BOT_CURSOR_BIN
export BOT_BP_DOCS_DIR BOT_BP_DOCS_DIR_ABS
