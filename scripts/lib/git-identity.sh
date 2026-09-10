#!/bin/bash
# Shared git/GitHub identity helpers.
#
# The bot commits and pushes as a different GitHub account than the human who
# owns the machine. Every mechanism here is scoped to either the target repo
# (.git/config) or the current process (env vars). Nothing writes to
# ~/.gitconfig or ~/.ssh/config, so other terminals are unaffected.

# Build the ssh command that pins git to a specific identity.
#
# IdentitiesOnly=yes is not optional: without it ssh-agent offers whatever keys
# it holds, and GitHub authenticates as the owner of the first one it accepts —
# which is the machine owner, not the bot, regardless of -i.
bot_ssh_command() {
  local key="$1"
  [ -n "$key" ] || return 1
  printf 'ssh -o IdentitiesOnly=yes -i %q' "$key"
}

# Echo the GitHub login an identity authenticates as, or nothing if it fails.
# `ssh -T git@github.com` always exits non-zero, so parse the greeting instead.
#
# </dev/null keeps ssh from swallowing the caller's stdin, and the trailing
# `return 0` keeps a rejected key from tripping `set -e` in the caller — the
# result is communicated by stdout being empty, not by exit status.
bot_ssh_login() {
  local key="$1" out
  out=$(ssh -o BatchMode=yes -o IdentitiesOnly=yes -i "$key" -T git@github.com </dev/null 2>&1)
  if [[ "$out" =~ ^Hi\ ([A-Za-z0-9._-]+)! ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
  return 0
}

# Fingerprint of a key file, or empty when ssh-keygen cannot read one.
#
# </dev/null matters: for an encrypted PEM key the public half is only
# recoverable with the passphrase, and without it ssh-keygen would prompt.
bot_key_fingerprint() {
  ssh-keygen -l -f "$1" </dev/null 2>/dev/null | awk '{print $2}'
}

# List identity files in ~/.ssh that ssh -i can use, one entry per key.
#
# Private keys are sniffed by content, since extensions lie: a PEM key named
# *.ppk is still a usable key, and globbing id_* misses it.
#
# Pairing is resolved twice, because neither rule catches everything:
#
#   By name — a private key beside its <key>.pub is dropped in favour of the
#   .pub. ssh matches the .pub against ssh-agent, then falls back to the
#   private file beside it, so the .pub is the strictly more capable target.
#
#   By fingerprint — a .pub is dropped when a listed private key turns out to
#   be its other half under an unrelated name (netzenbot.pub / netzenbot.ppk).
#   The name rule cannot see that pair, and listing both halves offers the
#   same key twice: once as a path that works with or without ssh-agent, and
#   once as a path that only works while the agent holds it, since ssh has no
#   <key> beside the .pub to fall back to.
#
# A key whose fingerprint is unreadable stays listed under both its names —
# for an encrypted PEM key held in the agent, -i on the private file cannot
# reach the agent copy and only the .pub works, so neither half is redundant.
bot_list_ssh_keys() {
  local f fp privs=() priv_fps=""

  # Private keys first: the .pub pass needs their fingerprints.
  for f in "$HOME"/.ssh/*; do
    [ -f "$f" ] || continue
    [ -f "$f.pub" ] && continue
    case "$f" in
      *.pub | */config | */known_hosts*) continue ;;
    esac
    if head -1 "$f" 2>/dev/null | grep -q -- '-----BEGIN .*PRIVATE KEY-----'; then
      privs+=("$f")
      fp=$(bot_key_fingerprint "$f")
      [ -n "$fp" ] && priv_fps="$priv_fps $fp"
    fi
  done

  for f in "$HOME"/.ssh/*.pub; do
    [ -f "$f" ] || continue
    fp=$(bot_key_fingerprint "$f")
    if [ -n "$fp" ]; then
      case " $priv_fps " in
        *" $fp "*) continue ;;
      esac
    fi
    printf '%s\n' "$f"
  done

  if [ ${#privs[@]} -gt 0 ]; then
    printf '%s\n' "${privs[@]}"
  fi
}

# True if this identity only works while ssh-agent holds the key — which an
# unattended run started from cron or launchd will not.
bot_identity_needs_agent() {
  local id="$1" priv="$1"
  case "$id" in
    *.pub) priv="${id%.pub}" ;;
  esac
  [ -f "$priv" ] || return 0
  # -P '' makes ssh-keygen fail rather than prompt when the key is encrypted.
  ssh-keygen -y -P '' -f "$priv" </dev/null >/dev/null 2>&1 && return 1
  return 0
}

# Pin identity and ssh key on a repo. Writes only to <repo>/.git/config.
# Resolve the public key the bot signs commits with.
#
# Defaults to the key it pushes with: one identity for the account means a
# commit authored by the bot is signed by that same bot, which is what makes
# GitHub mark it Verified. Signing with the machine owner's key instead yields
# "unknown_key" on every bot commit.
#
# ssh signing wants the *public* half. Accepts both the id_ed25519 ->
# id_ed25519.pub and the netzenbot.ppk -> netzenbot.pub spellings, and falls
# back to the private key, from which ssh-keygen derives the public part.
bot_signing_key() {
  local override="$1" ssh_key="$2" key
  key="${override:-$ssh_key}"
  [ -n "$key" ] || return 0

  case "$key" in
    *.pub) printf '%s\n' "$key"; return 0 ;;
  esac
  if [ -f "$key.pub" ]; then
    printf '%s\n' "$key.pub"
  elif [ -f "${key%.*}.pub" ]; then
    printf '%s\n' "${key%.*}.pub"
  else
    printf '%s\n' "$key"
  fi
}

# Configure a repo to sign commits and tags as the bot.
#
# Set locally, never globally: the machine owner's own signing config is left
# alone, and this repo stops inheriting it.
bot_apply_repo_signing() {
  local repo="$1" signing_key="$2"
  if [ -z "$signing_key" ]; then
    # No bot key configured — leave whatever the owner has set rather than
    # half-configuring signing.
    return 0
  fi
  git -C "$repo" config --local gpg.format ssh
  git -C "$repo" config --local user.signingkey "$signing_key"
  git -C "$repo" config --local commit.gpgsign true
  git -C "$repo" config --local tag.gpgsign true
  return 0
}

bot_apply_repo_identity() {
  local repo="$1" user="$2" email="$3" key="$4" signing_override="$5"
  [ -n "$user" ] && git -C "$repo" config --local user.name "$user"
  [ -n "$email" ] && git -C "$repo" config --local user.email "$email"
  if [ -n "$key" ]; then
    git -C "$repo" config --local core.sshCommand "$(bot_ssh_command "$key")"
  else
    git -C "$repo" config --local --unset core.sshCommand 2>/dev/null || true
  fi
  bot_apply_repo_signing "$repo" "$(bot_signing_key "$5" "$key")"
  return 0
}

# Pin the current process to the bot's GitHub identity. Both variables are
# inherited by every child — the agent, and each git and gh it runs — and die
# with the process. Other terminals, ~/.gitconfig, ~/.ssh/config and gh's
# active account are untouched.
bot_export_identity_env() {
  local key="$1" gh_account="$2" token

  if [ -n "$key" ]; then
    if [ ! -r "$key" ]; then
      echo "Error: bot.sshKeyPath is set but not readable: $key" >&2
      echo "  Fix the path or clear it by re-running 'make setup'." >&2
      return 1
    fi
    GIT_SSH_COMMAND=$(bot_ssh_command "$key")
    export GIT_SSH_COMMAND
  fi

  # An explicit GH_TOKEN in the environment wins — CI sets it that way.
  if [ -z "${GH_TOKEN:-}" ] && [ -n "$gh_account" ]; then
    # `gh auth token --user` reads a stored token without making that account
    # active, so gh keeps behaving normally everywhere else.
    token=$(gh auth token --user "$gh_account" 2>/dev/null || echo "")
    if [ -n "$token" ]; then
      export GH_TOKEN="$token"
    else
      echo "Warning: gh has no stored token for '$gh_account'." >&2
      echo "  PRs and comments would be posted by gh's active account instead." >&2
      echo "  Run 'make setup' for instructions." >&2
    fi
  fi

  return 0
}
