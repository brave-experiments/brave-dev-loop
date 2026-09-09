#!/bin/bash
# Setup script for brave-dev-loop
# Fully idempotent — safe to re-run at any time.
# Creates config.json, data files, hooks, and org-members cache as needed.
# Never overwrites existing files without an explicit confirmation.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK_SOURCE="$PROJECT_ROOT/hooks/pre-commit"
CONFIG_FILE="$PROJECT_ROOT/config.json"

source "$SCRIPT_DIR/lib/git-identity.sh"

echo "==================================="
echo "  Brave Dev Loop Setup"
echo "==================================="
echo ""

# ─── Step 1: config.json ─────────────────────────────────────────────────────

if [ -f "$CONFIG_FILE" ]; then
  echo "✓ config.json found — reconfiguring with current values as defaults"
  echo ""
  WRITE_CONFIG=true
else
  if [ ! -t 0 ]; then
    echo "Error: No config.json found and no interactive terminal available."
    echo "  Either run setup.sh directly from a terminal, or create config.json first:"
    echo "    cp config.example.json config.json   # then edit with your values"
    echo "    cp config.brave-core.json config.json # for existing brave-core deployments"
    exit 1
  fi
  echo "No config.json found — starting setup wizard."
  echo ""
  WRITE_CONFIG=true
fi

if [ "$WRITE_CONFIG" = true ]; then
  # Load previous values if config exists (for use as defaults)
  if [ -f "$CONFIG_FILE" ]; then
    _prev() { jq -r "$1 // empty" "$CONFIG_FILE" 2>/dev/null; }
    PREV_PR_REPO=$(_prev '.project.prRepository')
    PREV_DEFAULT_BRANCH=$(_prev '.project.defaultBranch')
    PREV_ISSUE_REPO=$(_prev '.project.issueRepository')
    PREV_BOT_USER=$(_prev '.bot.username')
    PREV_BOT_EMAIL=$(_prev '.bot.email')
    PREV_LABELS=$(jq -r '(.labels.issueLabels // []) | join(",")' "$CONFIG_FILE" 2>/dev/null)
    PREV_SSH_KEY=$(_prev '.bot.sshKeyPath')
    PREV_GH_ACCOUNT=$(_prev '.bot.ghAccount')
  fi

  prompt_required() {
    local var_name="$1" prompt_text="$2" default="$3" value=""
    while [ -z "$value" ]; do
      if [ -n "$default" ]; then
        read -p "$prompt_text[$default]: " value
        value="${value:-$default}"
      else
        read -p "$prompt_text" value
      fi
      if [ -z "$value" ]; then
        echo "  ⚠️  This field is required."
      fi
    done
    eval "$var_name=\$value"
  }

  echo "─── Target Project ───"
  prompt_required CFG_PR_REPO "Repo where the bot creates PRs (owner/repo): " "$PREV_PR_REPO"
  read -p "Default branch for ${CFG_PR_REPO} [${PREV_DEFAULT_BRANCH:-master}]: " CFG_DEFAULT_BRANCH
  CFG_DEFAULT_BRANCH="${CFG_DEFAULT_BRANCH:-${PREV_DEFAULT_BRANCH:-master}}"
  prompt_required CFG_ISSUE_REPO "Repo where issues/backlog lives (owner/repo): " "$PREV_ISSUE_REPO"

  # Derive org and project name from PR repo
  CFG_ORG="${CFG_PR_REPO%%/*}"
  CFG_PROJECT_NAME="${CFG_PR_REPO##*/}"

  echo ""
  echo "─── Target Repository Path ───"
  echo "Path to the git repo where the bot commits code."
  echo "Relative to the bot directory ($PROJECT_ROOT) or absolute."
  # Load previous value from config.json (with prd.json fallback for migration)
  PREV_TARGET_REPO=""
  if [ -f "$CONFIG_FILE" ]; then
    PREV_TARGET_REPO=$(jq -r '.project.targetRepoPath // empty' "$CONFIG_FILE" 2>/dev/null || echo "")
  fi
  prompt_required CFG_TARGET_REPO "Target repo path (e.g. ../src/brave, /abs/path/to/repo): " "$PREV_TARGET_REPO"

  echo ""
  echo "─── PRD Mode ───"
  echo "curated: you author data/prd.json and the bot works through those stories."
  echo "auto:    the PRD is a cache — the bot rebuilds it from issues assigned to"
  echo "         it and its open PRs before each run. No curation, no tokens."
  PREV_PRD_MODE=""
  if [ -f "$CONFIG_FILE" ]; then
    PREV_PRD_MODE=$(jq -r '.project.prdMode // empty' "$CONFIG_FILE" 2>/dev/null || echo "")
  fi
  read -p "PRD mode (curated/auto) [${PREV_PRD_MODE:-curated}]: " CFG_PRD_MODE
  CFG_PRD_MODE="${CFG_PRD_MODE:-${PREV_PRD_MODE:-curated}}"
  if [ "$CFG_PRD_MODE" != "auto" ]; then
    CFG_PRD_MODE="curated"
  fi

  PREV_OWNER_HANDLE=""
  if [ -f "$CONFIG_FILE" ]; then
    PREV_OWNER_HANDLE=$(jq -r '.project.botOwnerGithubHandle // empty' "$CONFIG_FILE" 2>/dev/null || echo "")
  fi
  read -p "Owner GitHub handle for stalled-review escalation (optional) [${PREV_OWNER_HANDLE:-}]: " CFG_OWNER_HANDLE
  CFG_OWNER_HANDLE="${CFG_OWNER_HANDLE:-$PREV_OWNER_HANDLE}"

  echo ""
  echo "─── Bot Identity ───"
  prompt_required CFG_BOT_USER "GitHub username the bot commits as: " "$PREV_BOT_USER"
  prompt_required CFG_BOT_EMAIL "Email for git commits: " "$PREV_BOT_EMAIL"
  read -p "Issue labels (comma-separated) [${PREV_LABELS:-}]: " CFG_LABELS_RAW
  CFG_LABELS_RAW="${CFG_LABELS_RAW:-$PREV_LABELS}"

  echo ""
  echo "─── SSH Key ───"
  echo "The bot pushes over SSH as $CFG_BOT_USER. If this machine's default key"
  echo "belongs to a different GitHub account, those pushes are rejected."
  echo "The choice is stored in the target repo's .git/config and in the bot's"
  echo "own environment — your ~/.ssh/config and other repos are not touched."
  echo ""

  # No mapfile: /bin/bash on macOS is 3.2.
  SSH_KEYS=()
  while IFS= read -r _key; do
    SSH_KEYS+=("$_key")
  done < <(bot_list_ssh_keys)
  CFG_SSH_KEY=""

  if [ ${#SSH_KEYS[@]} -eq 0 ]; then
    echo "  No private keys found in ~/.ssh."
    echo "  Generate one for the bot, add the public half to $CFG_BOT_USER on GitHub,"
    echo "  then re-run 'make setup':"
    echo "    ssh-keygen -t ed25519 -C \"$CFG_BOT_EMAIL\" -f ~/.ssh/${CFG_BOT_USER}_ed25519"
  else
    DEFAULT_CHOICE=0
    for i in "${!SSH_KEYS[@]}"; do
      marker=""
      if bot_identity_needs_agent "${SSH_KEYS[$i]}"; then
        marker="  [needs ssh-agent]"
      fi
      if [ "${SSH_KEYS[$i]}" = "$PREV_SSH_KEY" ]; then
        marker="$marker  (current)"
        DEFAULT_CHOICE=$((i + 1))
      fi
      echo "  $((i + 1))) ${SSH_KEYS[$i]}$marker"
    done
    echo "  0) Use this machine's default key (no pinning)"
    echo ""

    while true; do
      read -p "Select the key the bot pushes with [$DEFAULT_CHOICE]: " KEY_CHOICE
      KEY_CHOICE="${KEY_CHOICE:-$DEFAULT_CHOICE}"

      if [ "$KEY_CHOICE" = "0" ]; then
        CFG_SSH_KEY=""
        echo "  Skipped — git will use whatever ~/.ssh/config resolves for github.com."
        break
      fi

      if ! [[ "$KEY_CHOICE" =~ ^[0-9]+$ ]] || [ "$KEY_CHOICE" -gt ${#SSH_KEYS[@]} ]; then
        echo "  ⚠️  Enter a number between 0 and ${#SSH_KEYS[@]}."
        continue
      fi

      CANDIDATE="${SSH_KEYS[$((KEY_CHOICE - 1))]}"
      echo "  Checking which account $CANDIDATE authenticates as..."
      SSH_LOGIN=$(bot_ssh_login "$CANDIDATE")

      if [ "$SSH_LOGIN" = "$CFG_BOT_USER" ]; then
        echo "  ✓ Authenticates as $SSH_LOGIN"
        if bot_identity_needs_agent "$CANDIDATE"; then
          echo "  ⚠️  This key is passphrase-protected and only works while"
          echo "     ssh-agent holds it. Scheduled runs (make schedules) start"
          echo "     without an agent and will fail to push."
        fi
        CFG_SSH_KEY="$CANDIDATE"
        break
      fi

      if [ -z "$SSH_LOGIN" ]; then
        echo "  ⚠️  GitHub rejected this key — it is not registered on any account,"
        echo "     or it is passphrase-protected and not loaded in ssh-agent."
      else
        echo "  ⚠️  This key authenticates as '$SSH_LOGIN', not '$CFG_BOT_USER'."
        echo "     Pushes to $CFG_BOT_USER's fork would be rejected."
      fi
      read -p "  Use it anyway? (y/N) " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        CFG_SSH_KEY="$CANDIDATE"
        break
      fi
    done
  fi

  # Values go through the environment, not argv or string interpolation, so no
  # user input can be read as JSON or as shell.
  CFG_PROJECT_NAME="$CFG_PROJECT_NAME" \
  CFG_ORG="$CFG_ORG" \
  CFG_PR_REPO="$CFG_PR_REPO" \
  CFG_ISSUE_REPO="$CFG_ISSUE_REPO" \
  CFG_DEFAULT_BRANCH="$CFG_DEFAULT_BRANCH" \
  CFG_TARGET_REPO="$CFG_TARGET_REPO" \
  CFG_USE_FORK="${CFG_USE_FORK:-true}" \
  CFG_PROFILE="${CFG_PROFILE:-}" \
  CFG_PRD_MODE="${CFG_PRD_MODE:-curated}" \
  CFG_OWNER_HANDLE="$CFG_OWNER_HANDLE" \
  CFG_BOT_USER="$CFG_BOT_USER" \
  CFG_BOT_EMAIL="$CFG_BOT_EMAIL" \
  CFG_SSH_KEY="$CFG_SSH_KEY" \
  CFG_GH_ACCOUNT="${PREV_GH_ACCOUNT:-}" \
  CFG_LABELS_RAW="$CFG_LABELS_RAW" \
  CONFIG_FILE="$CONFIG_FILE" \
  BOT_ROOT="$PROJECT_ROOT" \
  python3 -c "
import json, os

def val(name):
    return os.environ.get(name) or None

target_repo = os.environ['CFG_TARGET_REPO']
bot_root = os.environ['BOT_ROOT']

# Derive docsDir from the *resolved* target repo so it does not depend on which
# base the operator typed the path against. targetRepoPath is accepted relative
# to the bot dir or to its parent (see resolve_target_repo in load-config.sh);
# docsDir is always stored relative to the bot dir.
if os.path.isabs(target_repo):
    _target_abs = os.path.normpath(target_repo)
else:
    _bot_base = os.path.normpath(os.path.join(bot_root, target_repo))
    _parent_base = os.path.normpath(os.path.join(os.path.dirname(bot_root), target_repo))
    if os.path.exists(os.path.join(_bot_base, '.git')):
        _target_abs = _bot_base
    elif os.path.exists(os.path.join(_parent_base, '.git')):
        _target_abs = _parent_base
    else:
        _target_abs = _bot_base
docs_dir = os.path.join(os.path.relpath(_target_abs, bot_root), 'docs')
config = {
    'project': {
        'name': os.environ['CFG_PROJECT_NAME'],
        'org': os.environ['CFG_ORG'],
        'prRepository': os.environ['CFG_PR_REPO'],
        'issueRepository': os.environ['CFG_ISSUE_REPO'],
        'defaultBranch': os.environ['CFG_DEFAULT_BRANCH'],
        'targetRepoPath': target_repo,
        'useFork': os.environ.get('CFG_USE_FORK', 'true') == 'true',
        'profile': os.environ.get('CFG_PROFILE') or 'default',
        'prdMode': os.environ.get('CFG_PRD_MODE') or 'curated',
        'botOwnerGithubHandle': val('CFG_OWNER_HANDLE'),
    },
    'bot': {
        'username': os.environ['CFG_BOT_USER'],
        'email': os.environ['CFG_BOT_EMAIL'],
        'sshKeyPath': val('CFG_SSH_KEY'),
        'ghAccount': val('CFG_GH_ACCOUNT'),
        'ghConfigDir': None,
        'agent': 'claude',
        'claudeModel': 'opus',
        'claudeBin': None,
        'codexModel': None,
        'codexBin': None,
        'cursorModel': None,
        'cursorBin': None,
    },
    'labels': {
        'prLabels': ['ai-generated'],
        'issueLabels': [l.strip() for l in os.environ['CFG_LABELS_RAW'].split(',') if l.strip()],
        'disabledTestLabel': '',
    },
    'bestPractices': {
        'docsDir': docs_dir,
        'indexFile': 'best_practices.md',
        'securityFile': 'SECURITY.md',
    },
    'schedules': {
        'syncRepo': False,
        'syncRepoPath': None,
    },
}
with open(os.environ['CONFIG_FILE'], 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\\n')
"
  echo ""
  echo "✓ Config written to $CONFIG_FILE"
  echo ""
fi

# ─── Step 1b: Repair paths in an existing config ─────────────────────────────
#
# Fixes a docsDir left pointing nowhere by the historical base ambiguity
# between targetRepoPath consumers. No-op when already correct, so it is safe
# on every run, wizard or not.
if [ -f "$CONFIG_FILE" ]; then
  python3 "$SCRIPT_DIR/repair-config-paths.py" --config "$CONFIG_FILE" --bot-root "$PROJECT_ROOT"
fi

# Source config (needed for all subsequent steps)
source "$SCRIPT_DIR/lib/load-config.sh"

# ─── Step 2: Data files ──────────────────────────────────────────────────────

mkdir -p "$PROJECT_ROOT/data"

create_if_missing() {
  local target="$1" template="$2" label="$3"
  if [ -f "$target" ]; then
    echo "✓ $label already exists"
  elif [ -f "$template" ]; then
    cp "$template" "$target"
    echo "✓ $label created from template"
  else
    echo "⚠️  $label template not found at $template"
  fi
}

create_if_missing "$PROJECT_ROOT/data/prd.json" \
  "$PROJECT_ROOT/data/prd.example.json" "data/prd.json"
create_if_missing "$PROJECT_ROOT/data/run-state.json" \
  "$PROJECT_ROOT/data/run-state.example.json" "data/run-state.json"
create_if_missing "$PROJECT_ROOT/data/progress.txt" \
  "$PROJECT_ROOT/data/progress.example.txt" "data/progress.txt"

# ─── Step 2b: .envrc ─────────────────────────────────────────────────────────
#
# Every cron line begins `cd $PROJECT_ROOT && source .envrc && ...`, so a
# missing .envrc returns 1 and silently cancels the whole job. It is also the
# only hook the scheduled skill jobs have for the bot identity: they invoke
# claude directly rather than through run.sh, so without this they would act as
# whichever account gh happens to have active.
if [ -f "$PROJECT_ROOT/.envrc" ]; then
  echo "✓ .envrc already exists"
else
  cat > "$PROJECT_ROOT/.envrc" <<'ENVRC'
# Generated by scripts/setup.sh. Gitignored — safe to edit.
# Pins this repo's git and gh operations to the bot identity from config.json.
# Sourced by every cron job, and by direnv for interactive shells here.
_bot_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
_bot_dir="${_bot_dir:-$PWD}"
source "$_bot_dir/scripts/lib/load-config.sh"
source "$_bot_dir/scripts/lib/git-identity.sh"

# When bot.ghConfigDir is set, gh reads its credentials from there instead of
# ~/.config/gh. The bot's login never enters your personal gh config, so no
# account is ever added, switched, or made active outside this directory.
if [ -n "$BOT_GH_CONFIG_DIR" ] && [ -d "$BOT_GH_CONFIG_DIR" ]; then
  export GH_CONFIG_DIR="$BOT_GH_CONFIG_DIR"
fi

bot_export_identity_env "$BOT_SSH_KEY_PATH" "$BOT_GH_ACCOUNT" || true
unset _bot_dir
ENVRC
  echo "✓ .envrc created (pins git + gh to the bot identity)"
fi
echo ""

# ─── Step 3: Org members cache ───────────────────────────────────────────────

ORG_MEMBERS_FILE="$PROJECT_ROOT/.ignore/org-members.txt"
mkdir -p "$PROJECT_ROOT/.ignore"

if [ -f "$ORG_MEMBERS_FILE" ]; then
  echo "✓ Org members file found: $(wc -l < "$ORG_MEMBERS_FILE") members"
else
  echo "No org members cache found."
  read -p "  Generate it now via GitHub API? (Y/n) " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    echo "  Fetching members of $BOT_ORG..."
    if gh api "/orgs/$BOT_ORG/members" --paginate --jq '.[].login' > "$ORG_MEMBERS_FILE" 2>/dev/null; then
      echo "  ✓ Wrote $(wc -l < "$ORG_MEMBERS_FILE") members to $ORG_MEMBERS_FILE"
    else
      echo "  ⚠️  Failed to fetch org members (check gh auth and org access)"
      echo "     Create it manually: one GitHub username per line"
      rm -f "$ORG_MEMBERS_FILE"
    fi
  else
    echo "  Skipped. Create it manually when ready:"
    echo "    gh api /orgs/$BOT_ORG/members --paginate --jq '.[].login' > $ORG_MEMBERS_FILE"
  fi
fi
echo ""

# ─── Step 4: Target repo git config + hooks ──────────────────────────────────

SKIP_GIT=false

# Use value from wizard if we ran it, otherwise read from config.json
if [ -n "${CFG_TARGET_REPO:-}" ]; then
  GIT_REPO="$CFG_TARGET_REPO"
else
  GIT_REPO="$BOT_TARGET_REPO_PATH"
fi

if [ -z "$GIT_REPO" ]; then
  echo "ℹ️  No target repo path configured — skipping git identity and hooks."
  SKIP_GIT=true
fi

if [ "$SKIP_GIT" = false ]; then
  GIT_REPO_RAW="$GIT_REPO"
  # Accepts a path relative to the bot dir or to its parent, or an absolute one.
  GIT_REPO="$(resolve_target_repo "$GIT_REPO")"

  if [ ! -d "$GIT_REPO/.git" ]; then
    echo "⚠️  $GIT_REPO is not a git repository — skipping target repo setup."
    SKIP_GIT=true
  fi
fi

if [ "$SKIP_GIT" = false ]; then
  echo "Target git repository: $GIT_REPO"
  cd "$GIT_REPO"

  # --local, not plain `git config`: a plain read falls through to ~/.gitconfig,
  # so a machine owner with a global identity would look already-configured and
  # the bot would silently commit under their name.
  GIT_USER=$(git -C "$GIT_REPO" config --local user.name || echo "")
  GIT_EMAIL=$(git -C "$GIT_REPO" config --local user.email || echo "")
  GIT_SSH_CFG=$(git -C "$GIT_REPO" config --local core.sshCommand || echo "")

  # Use the key from the wizard if it just ran, otherwise the stored one.
  if [ -n "${CFG_TARGET_REPO:-}" ]; then
    SSH_KEY="${CFG_SSH_KEY:-}"
  else
    SSH_KEY="$BOT_SSH_KEY_PATH"
  fi

  EXPECTED_SSH_CFG=""
  if [ -n "$SSH_KEY" ]; then
    EXPECTED_SSH_CFG=$(bot_ssh_command "$SSH_KEY")
  fi

  # Defaults to the key the bot pushes with, so the signature belongs to the
  # same account as the authorship. Without this the repo inherits the machine
  # owner's global signing key and GitHub marks every bot commit unverified.
  SIGNING_KEY=$(bot_signing_key "$BOT_SIGNING_KEY_PATH" "$SSH_KEY")
  GIT_SIGNING_KEY=$(git -C "$GIT_REPO" config --local user.signingkey || echo "")

  if [ "$GIT_USER" = "$BOT_USERNAME" ] && [ "$GIT_EMAIL" = "$BOT_EMAIL" ] &&
     [ "$GIT_SSH_CFG" = "$EXPECTED_SSH_CFG" ] &&
     [ "$GIT_SIGNING_KEY" = "$SIGNING_KEY" ]; then
    echo "  ✓ Git identity: $GIT_USER <$GIT_EMAIL>"
    if [ -n "$EXPECTED_SSH_CFG" ]; then
      echo "  ✓ SSH key pinned: $SSH_KEY"
    fi
    if [ -n "$SIGNING_KEY" ]; then
      echo "  ✓ Commits signed with: $SIGNING_KEY"
    fi
  else
    echo "  Repo-local git identity needs updating:"
    echo "    user.name       $GIT_USER → $BOT_USERNAME"
    echo "    user.email      $GIT_EMAIL → $BOT_EMAIL"
    if [ -n "$EXPECTED_SSH_CFG" ]; then
      echo "    core.sshCommand → pins pushes to $SSH_KEY"
    elif [ -n "$GIT_SSH_CFG" ]; then
      echo "    core.sshCommand → unset (falls back to this machine's default key)"
    fi
    if [ -n "$SIGNING_KEY" ]; then
      echo "    user.signingkey → signs commits and tags with $SIGNING_KEY"
    fi
    echo "  These are written to $GIT_REPO/.git/config only."
    read -p "  Apply? (Y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
      bot_apply_repo_identity "$GIT_REPO" "$BOT_USERNAME" "$BOT_EMAIL" "$SSH_KEY" "$BOT_SIGNING_KEY_PATH"
      GIT_USER="$BOT_USERNAME"
      GIT_EMAIL="$BOT_EMAIL"
      echo "  ✓ Git identity configured"
    else
      echo "  Skipped. Configure manually:"
      echo "    git -C $GIT_REPO config --local user.name \"$BOT_USERNAME\""
      echo "    git -C $GIT_REPO config --local user.email \"$BOT_EMAIL\""
      if [ -n "$EXPECTED_SSH_CFG" ]; then
        echo "    git -C $GIT_REPO config --local core.sshCommand \"$EXPECTED_SSH_CFG\""
      fi
      if [ -n "$SIGNING_KEY" ]; then
        echo "    git -C $GIT_REPO config --local gpg.format ssh"
        echo "    git -C $GIT_REPO config --local user.signingkey \"$SIGNING_KEY\""
        echo "    git -C $GIT_REPO config --local commit.gpgsign true"
      fi
      GIT_USER="${GIT_USER:-$BOT_USERNAME}"
    fi
  fi

  # ─── Configure git remotes ────────────────────────────────────────────────
  # Two supported layouts, selected by project.useFork:
  #   true  (default) — origin = bot's fork, upstream = main repo
  #   false           — origin = upstream = main repo, for a bot with write
  #                     access. No fork is created and none is expected.
  # Default true so existing fork-based deployments are unaffected.
  FORK_REPO_NAME="${BOT_PR_REPO##*/}"
  EXPECTED_ORIGIN="https://github.com/$BOT_USERNAME/$FORK_REPO_NAME.git"
  EXPECTED_ORIGIN_SSH="git@github.com:$BOT_USERNAME/$FORK_REPO_NAME.git"
  EXPECTED_UPSTREAM="https://github.com/$BOT_PR_REPO.git"
  EXPECTED_UPSTREAM_SSH="git@github.com:$BOT_PR_REPO.git"

  CURRENT_ORIGIN=$(git -C "$GIT_REPO" remote get-url origin 2>/dev/null || echo "")
  CURRENT_UPSTREAM=$(git -C "$GIT_REPO" remote get-url upstream 2>/dev/null || echo "")

  # Helper: check if a URL matches expected (HTTPS or SSH)
  url_matches() {
    local actual="$1" expected_https="$2" expected_ssh="$3"
    local norm_actual="${actual%.git}" norm_https="${expected_https%.git}" norm_ssh="${expected_ssh%.git}"
    [ "$norm_actual" = "$norm_https" ] || [ "$norm_actual" = "$norm_ssh" ]
  }

  origin_is_main_repo() {
    local norm="${1%.git}"
    [ "$norm" = "${EXPECTED_UPSTREAM%.git}" ] || [ "$norm" = "${EXPECTED_UPSTREAM_SSH%.git}" ]
  }

  # Build a list of changes needed
  REMOTE_ACTIONS=()
  REMOTES_OK=true

  USE_FORK=$(bot_config_bool '.project.useFork')
  if [ -z "$USE_FORK" ]; then
    USE_FORK=true
  fi

  if [ "$USE_FORK" != "true" ]; then
    # No-fork layout: origin and upstream both point at the main repo. The bot
    # pushes branches straight to it, and PR refs (refs/pull/N/head) resolve
    # through origin, which the review worktrees depend on.
    if [ -z "$CURRENT_ORIGIN" ]; then
      REMOTE_ACTIONS+=("Add origin → $BOT_PR_REPO ($EXPECTED_UPSTREAM_SSH)")
    elif ! url_matches "$CURRENT_ORIGIN" "$EXPECTED_UPSTREAM" "$EXPECTED_UPSTREAM_SSH"; then
      REMOTE_ACTIONS+=("Set origin → $BOT_PR_REPO ($EXPECTED_UPSTREAM_SSH)")
    fi
    if [ -n "$CURRENT_UPSTREAM" ] && ! url_matches "$CURRENT_UPSTREAM" "$EXPECTED_UPSTREAM" "$EXPECTED_UPSTREAM_SSH"; then
      REMOTE_ACTIONS+=("Set upstream → $BOT_PR_REPO ($EXPECTED_UPSTREAM_SSH)")
    fi

    if [ ${#REMOTE_ACTIONS[@]} -eq 0 ]; then
      echo "  ✓ origin → $BOT_PR_REPO (no-fork layout)"
    else
      echo ""
      echo "  The following remote changes are needed:"
      for action in "${REMOTE_ACTIONS[@]}"; do
        echo "    • $action"
      done
      echo ""
      read -p "  Apply these remote changes? (Y/n) " -n 1 -r
      echo
      if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        if [ -z "$CURRENT_ORIGIN" ]; then
          git -C "$GIT_REPO" remote add origin "$EXPECTED_UPSTREAM_SSH"
        else
          git -C "$GIT_REPO" remote set-url origin "$EXPECTED_UPSTREAM_SSH"
        fi
        if [ -n "$CURRENT_UPSTREAM" ]; then
          git -C "$GIT_REPO" remote set-url upstream "$EXPECTED_UPSTREAM_SSH"
        fi
        echo "  ✓ origin → $BOT_PR_REPO"
      else
        echo "  Skipped. Configure manually:"
        echo "    cd $GIT_REPO"
        echo "    git remote set-url origin $EXPECTED_UPSTREAM_SSH"
      fi
    fi
    REMOTES_OK=false   # skip the fork-layout branch below
    FORK_EXISTS=false
  fi

  # Check if the bot's fork exists on GitHub
  FORK_EXISTS=false
  if [ "$USE_FORK" = "true" ]; then
  if gh repo view "$BOT_USERNAME/$FORK_REPO_NAME" --json name >/dev/null 2>&1; then
    FORK_EXISTS=true
  fi
  fi

  if [ "$USE_FORK" = "true" ] && [ "$FORK_EXISTS" = false ]; then
    echo ""
    echo "  Fork $BOT_USERNAME/$FORK_REPO_NAME not found on GitHub."
    read -p "  Create fork now? (Y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
      if gh repo fork "$BOT_PR_REPO" --clone=false 2>/dev/null; then
        echo "  ✓ Fork created: $BOT_USERNAME/$FORK_REPO_NAME"
        FORK_EXISTS=true
      else
        echo "  ⚠️  Failed to create fork. Create it manually:"
        echo "     https://github.com/$BOT_PR_REPO/fork"
        REMOTES_OK=false
      fi
    else
      echo "  Skipped. Create it manually: https://github.com/$BOT_PR_REPO/fork"
      REMOTES_OK=false
    fi
  fi

  if [ "$FORK_EXISTS" = true ]; then
    # Determine what remote changes are needed
    if [ -z "$CURRENT_ORIGIN" ]; then
      REMOTE_ACTIONS+=("Add origin → $BOT_USERNAME/$FORK_REPO_NAME ($EXPECTED_ORIGIN_SSH)")
    elif origin_is_main_repo "$CURRENT_ORIGIN"; then
      if [ -z "$CURRENT_UPSTREAM" ]; then
        REMOTE_ACTIONS+=("Rename origin → upstream (keeps $CURRENT_ORIGIN as upstream)")
      fi
      REMOTE_ACTIONS+=("Set origin → $BOT_USERNAME/$FORK_REPO_NAME ($EXPECTED_ORIGIN_SSH)")
    elif ! url_matches "$CURRENT_ORIGIN" "$EXPECTED_ORIGIN" "$EXPECTED_ORIGIN_SSH"; then
      echo "  ⚠️  origin points to unexpected URL: $CURRENT_ORIGIN"
      echo "     Expected: $EXPECTED_ORIGIN_SSH (or HTTPS equivalent)"
      REMOTES_OK=false
    fi

    if [ "$REMOTES_OK" = true ]; then
      # Re-read upstream in case origin was going to be renamed
      FUTURE_UPSTREAM="$CURRENT_UPSTREAM"
      if [ -z "$CURRENT_UPSTREAM" ] && origin_is_main_repo "$CURRENT_ORIGIN"; then
        FUTURE_UPSTREAM="$CURRENT_ORIGIN"
      fi

      if [ -z "$FUTURE_UPSTREAM" ]; then
        REMOTE_ACTIONS+=("Add upstream → $BOT_PR_REPO ($EXPECTED_UPSTREAM_SSH)")
      elif ! url_matches "$FUTURE_UPSTREAM" "$EXPECTED_UPSTREAM" "$EXPECTED_UPSTREAM_SSH"; then
        echo "  ⚠️  upstream points to unexpected URL: $CURRENT_UPSTREAM"
        echo "     Expected: $EXPECTED_UPSTREAM_SSH (or HTTPS equivalent)"
        REMOTES_OK=false
      fi
    fi
  fi

  # If everything is already correct, just report it
  if [ "$USE_FORK" = "true" ] && [ "$REMOTES_OK" = true ] && [ ${#REMOTE_ACTIONS[@]} -eq 0 ]; then
    echo "  ✓ origin → $BOT_USERNAME/$FORK_REPO_NAME"
    echo "  ✓ upstream → $BOT_PR_REPO"
    echo "  ✓ Remotes configured correctly"
  fi

  # If there are changes to make, describe them and ask for confirmation
  if [ "$USE_FORK" = "true" ] && [ "$REMOTES_OK" = true ] && [ ${#REMOTE_ACTIONS[@]} -gt 0 ]; then
    echo ""
    echo "  The following remote changes are needed:"
    for action in "${REMOTE_ACTIONS[@]}"; do
      echo "    • $action"
    done
    echo ""
    read -p "  Apply these remote changes? (Y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
      # Apply the changes
      if [ -z "$CURRENT_ORIGIN" ]; then
        git -C "$GIT_REPO" remote add origin "$EXPECTED_ORIGIN_SSH"
        echo "  ✓ origin added → $BOT_USERNAME/$FORK_REPO_NAME"
      elif origin_is_main_repo "$CURRENT_ORIGIN"; then
        if [ -z "$CURRENT_UPSTREAM" ]; then
          git -C "$GIT_REPO" remote rename origin upstream
          echo "  ✓ Renamed origin → upstream ($BOT_PR_REPO)"
          CURRENT_UPSTREAM="$CURRENT_ORIGIN"
          git -C "$GIT_REPO" remote add origin "$EXPECTED_ORIGIN_SSH"
        else
          git -C "$GIT_REPO" remote set-url origin "$EXPECTED_ORIGIN_SSH"
        fi
        echo "  ✓ origin → $BOT_USERNAME/$FORK_REPO_NAME"
      fi

      CURRENT_UPSTREAM=$(git -C "$GIT_REPO" remote get-url upstream 2>/dev/null || echo "")
      if [ -z "$CURRENT_UPSTREAM" ]; then
        git -C "$GIT_REPO" remote add upstream "$EXPECTED_UPSTREAM_SSH"
        echo "  ✓ upstream added → $BOT_PR_REPO"
      fi

      echo "  ✓ Remotes configured"
    else
      echo "  Skipped. Configure manually:"
      echo "    cd $GIT_REPO"
      echo "    git remote set-url origin $EXPECTED_ORIGIN_SSH"
      echo "    git remote add upstream $EXPECTED_UPSTREAM_SSH"
    fi
  fi

  # Install pre-commit hook for target repo
  if [ -n "$GIT_USER" ]; then
    HOOK_DEST="$GIT_REPO/.git/hooks/pre-commit"
    sed "s/__BOT_USERNAME__/$GIT_USER/g" "$HOOK_SOURCE" > "$HOOK_DEST"
    chmod +x "$HOOK_DEST"
    echo "  ✓ Pre-commit hook installed (blocks $GIT_USER from modifying dependencies)"
  fi
  echo ""
fi

# ─── Step 5: Bot repo hook ───────────────────────────────────────────────────

BOT_HOOK_SOURCE="$PROJECT_ROOT/hooks/pre-commit-bot-repo"
BOT_HOOK_DEST="$PROJECT_ROOT/.git/hooks/pre-commit"

cp "$BOT_HOOK_SOURCE" "$BOT_HOOK_DEST"
chmod +x "$BOT_HOOK_DEST"
echo "✓ Bot repo pre-commit hook installed"
echo "  (Prevents committing data/prd.json, data/progress.txt, data/run-state.json)"

# The bot commits to this repo too (learned patterns, best-practice updates), so
# it needs the same identity here. Without it these commits inherit the machine
# owner's name and signing key.
BOT_REPO_SIGNING_KEY=$(bot_signing_key "$BOT_SIGNING_KEY_PATH" "$BOT_SSH_KEY_PATH")
bot_apply_repo_identity "$PROJECT_ROOT" "$BOT_USERNAME" "$BOT_EMAIL" "$BOT_SSH_KEY_PATH" "$BOT_SIGNING_KEY_PATH"
echo "✓ Bot repo git identity: $BOT_USERNAME <$BOT_EMAIL>"
if [ -n "$BOT_REPO_SIGNING_KEY" ]; then
  echo "  Commits signed with: $BOT_REPO_SIGNING_KEY"
fi
echo ""

# A signing key GitHub does not know about produces "Unverified" on every
# commit, which looks identical to a broken signature. It must be registered as
# a *signing* key, which is a separate list from authentication keys.
if [ -n "$BOT_REPO_SIGNING_KEY" ] && [ -f "$BOT_REPO_SIGNING_KEY" ]; then
  SIGNING_PUB=$(awk '{print $1" "$2}' "$BOT_REPO_SIGNING_KEY" 2>/dev/null)
  # Reading this list needs the admin:ssh_signing_key scope. Distinguish "not
  # registered" from "cannot tell": warning about a key that is in fact
  # registered just trains the operator to ignore the warning.
  if ! SIGNING_KEYS=$(gh api user/ssh_signing_keys --jq '.[].key' 2>/dev/null); then
    echo "ℹ️  Could not check whether the signing key is registered for $BOT_USERNAME"
    echo "   (the gh token lacks the admin:ssh_signing_key scope — this does not"
    echo "   mean the key is missing). To check:"
    echo "     GH_CONFIG_DIR=${BOT_GH_CONFIG_DIR:-~/.config/gh} gh auth refresh -h github.com -s admin:ssh_signing_key"
    echo ""
  elif printf '%s\n' "$SIGNING_KEYS" | awk '{print $1" "$2}' | grep -Fxq "$SIGNING_PUB"; then
    echo "✓ Signing key is registered on the bot's GitHub account"
  else
    echo "⚠️  The signing key is not registered as a signing key for $BOT_USERNAME."
    echo "   Commits will be signed but show as Unverified on GitHub."
    echo "   Register it (authentication keys are a separate list — adding it"
    echo "   there is not enough):"
    echo "     GH_CONFIG_DIR=${BOT_GH_CONFIG_DIR:-~/.config/gh} gh ssh-key add $BOT_REPO_SIGNING_KEY --type signing --title \"$BOT_USERNAME signing key\""
  fi
  echo ""
fi

# ─── Step 6: GitHub CLI account ──────────────────────────────────────────────
# The ssh key only covers git transport. gh carries its own stored token, so
# without a matching account the bot's PRs and comments are posted by whoever
# owns this machine.

if gh auth token --user "$BOT_GH_ACCOUNT" >/dev/null 2>&1; then
  echo "✓ gh account '$BOT_GH_ACCOUNT' is authenticated"
  echo "  run.sh exports its token as GH_TOKEN for the bot process only —"
  echo "  gh's active account for your other terminals is left alone."
else
  ACTIVE_GH=$(gh api user --jq .login 2>/dev/null || echo "")
  if [ -n "$ACTIVE_GH" ]; then
    echo "⚠️  gh has no stored token for '$BOT_GH_ACCOUNT' (active account: $ACTIVE_GH)"
    echo "   Without one, PRs and comments are posted as $ACTIVE_GH, not the bot."
    echo "   Add the account, then switch back — 'gh auth login' makes the new"
    echo "   account active, but both tokens stay stored:"
    echo "     gh auth login --hostname github.com"
    echo "     gh auth switch --user $ACTIVE_GH"
  else
    echo "⚠️  gh is not authenticated for any account."
    echo "   Log in as $BOT_GH_ACCOUNT so the bot can open PRs and comment:"
    echo "     gh auth login --hostname github.com"
  fi
fi
echo ""

# ─── Step 7: Environment checks ──────────────────────────────────────────────

if [[ "$(uname)" == "Linux" ]]; then
  if ! command -v xvfb-run &> /dev/null; then
    echo "⚠️  xvfb-run not found (needed for browser tests in headless environments)"
    echo "   sudo apt-get install xvfb"
    echo ""
  else
    echo "✓ xvfb-run found"
  fi
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "==================================="
echo "  Setup Complete!"
echo "==================================="
echo ""
echo "  Project:    $BOT_PROJECT_NAME"
echo "  PR repo:    $BOT_PR_REPO"
echo "  Issue repo: $BOT_ISSUE_REPO"
echo ""

# Show next steps based on what's still needed
NEXT=()
if [ "$BOT_PRD_MODE" != "auto" ] && \
   { [ ! -f "$PROJECT_ROOT/data/prd.json" ] || \
     [ "$(jq -r '.stories // .stories | length' "$PROJECT_ROOT/data/prd.json" 2>/dev/null)" = "0" ]; }; then
  NEXT+=("Edit data/prd.json with your user stories (or use /prd-json skill)")
fi
if [ "$SKIP_GIT" = true ] && [ -z "${GIT_REPO_RAW:-}" ]; then
  NEXT+=("Re-run 'make setup' and provide the target repo path to configure git identity, remotes, and hooks")
elif [ "$SKIP_GIT" = true ]; then
  NEXT+=("Ensure $GIT_REPO_RAW exists as a git repository, then re-run 'make setup'")
fi

if [ ${#NEXT[@]} -gt 0 ]; then
  echo "Next steps:"
  for i in "${!NEXT[@]}"; do
    echo "  $((i+1)). ${NEXT[$i]}"
  done
  echo ""
fi

echo "Run the bot: ./run.sh"
echo ""
