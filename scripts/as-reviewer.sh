#!/bin/bash
# Run a command as the reviewer account, the second GitHub account that scheduled
# /review-prs jobs act as (see "Reviewer account" in docs/bot-identity.md).
#
# Usage: as-reviewer.sh [--] <command...>
#
# With no reviewer configured (reviewer.username in config.json) this runs the
# command unchanged, as the bot. With one configured it either runs the command as
# the reviewer or stops: a reviewer whose login is missing or belongs to someone
# else must not quietly fall back to the bot, because the whole point of the
# account is that reviews come from a different one.
#
# gh is pinned with GH_TOKEN, not just GH_CONFIG_DIR. GH_TOKEN outranks the config
# directory, and .envrc exports the bot's token into every cron job, so a bare
# GH_CONFIG_DIR would leave the bot in force. Claude Code's env settings can also
# overwrite GH_CONFIG_DIR inside the session, but not the token.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

[ "${1:-}" = "--" ] && shift
if [ $# -eq 0 ]; then
  echo "Usage: as-reviewer.sh [--] <command...>" >&2
  exit 2
fi

if [ -z "$BOT_REVIEWER_USERNAME" ]; then
  exec "$@"
fi

# A clean environment for the lookups: an inherited token would be read back as
# the reviewer's.
gh_as_reviewer() {
  env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
    GH_CONFIG_DIR="$BOT_REVIEWER_GH_CONFIG_DIR" gh "$@"
}

if ! token=$(gh_as_reviewer auth token --hostname github.com 2>/dev/null) || [ -z "$token" ]; then
  echo "Error: reviewer '$BOT_REVIEWER_USERNAME' has no gh login in $BOT_REVIEWER_GH_CONFIG_DIR." >&2
  echo "  Run 'make setup' to log it in, or clear reviewer.username in config.json" >&2
  echo "  to review as $BOT_USERNAME." >&2
  exit 1
fi

login=$(GH_TOKEN="$token" gh api user --jq .login 2>/dev/null || true)
if [ "$(tr '[:upper:]' '[:lower:]' <<< "$login")" != "$(tr '[:upper:]' '[:lower:]' <<< "$BOT_REVIEWER_USERNAME")" ]; then
  echo "Error: the gh login in $BOT_REVIEWER_GH_CONFIG_DIR resolves to '${login:-nobody}', not '$BOT_REVIEWER_USERNAME'." >&2
  echo "  Run 'make setup' to log the right account in." >&2
  exit 1
fi

unset GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
export GH_CONFIG_DIR="$BOT_REVIEWER_GH_CONFIG_DIR" GH_TOKEN="$token"
exec "$@"
