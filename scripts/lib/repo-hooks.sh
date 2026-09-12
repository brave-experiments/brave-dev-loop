#!/bin/bash
# Installing a git hook into a repo somebody else owns.
#
# Writing to <repo>/.git/hooks is only right when that repo has not moved its hooks. core.hooksPath
# *replaces* .git/hooks rather than adding to it, so a hook written to .git/hooks in a repo that
# sets it never runs and never says so. A target repo with its own checked-in hooks sets it, which
# is enough for a hook installed here to be silently dead.
#
# Resolving that path leads straight to the opposite hazard: the directory it names is usually
# inside the working tree and holds the version-controlled hooks it was set for, so a hook
# installed by name can land on top of one the repo tracks. Hence both halves here -- find where
# git looks, and refuse to overwrite what somebody committed there.

# Echo an absolute path, given one git reported that may be relative to the repo.
repo_abs_git_path() {
  local repo="$1" path="$2"
  case "$path" in
    /*) printf '%s\n' "$path" ;;
    *) printf '%s\n' "$repo/$path" ;;
  esac
}

# Echo the directory git will actually look for hooks in.
#
# A relative core.hooksPath is resolved against the top of the working tree, which is where git runs
# a hook from, not against the git directory. Where it is unset, `rev-parse --git-path` is what
# answers, rather than the git directory with /hooks appended: in a worktree those differ, because
# hooks are shared and live in the common directory while the worktree's own git directory holds
# only its HEAD and index.
repo_hooks_dir() {
  local repo="$1" configured
  configured=$(git -C "$repo" config --path core.hooksPath 2>/dev/null || true)

  if [ -z "$configured" ]; then
    repo_abs_git_path "$repo" "$(git -C "$repo" rev-parse --git-path hooks)"
    return 0
  fi

  case "$configured" in
    /*) printf '%s\n' "$configured" ;;
    *) printf '%s\n' "$(git -C "$repo" rev-parse --show-toplevel)/$configured" ;;
  esac
}

# Echo where a path sits relative to the repo's working tree, or nothing where it is outside that
# tree or inside the git directory, which `git status` never lists either way.
repo_worktree_relative() {
  local repo="$1" path="$2" toplevel git_dir
  toplevel=$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null) || return 0
  [ -n "$toplevel" ] || return 0

  # Compared as physical paths: git resolves symlinks in what it reports and $repo may not, so
  # /var and /private/var stand for the same directory and would not match as text.
  toplevel=$(cd "$toplevel" && pwd -P)
  git_dir=$(repo_abs_git_path "$repo" "$(git -C "$repo" rev-parse --git-common-dir)")
  git_dir=$(cd "$git_dir" && pwd -P)

  case "$path" in
    "$git_dir"/*) return 0 ;;
    "$toplevel"/*) printf '%s\n' "${path#"$toplevel"/}" ;;
  esac
}

# Install one hook into a repo, and keep it out of that repo's `git status`.
#
# Returns non-zero without writing anything where the repo tracks a hook of that name, so the caller
# can say so instead of claiming an install that would have destroyed committed work. That is not a
# setup failure: a repo with its own checked-in hooks is the normal arrangement.
repo_install_hook() {
  local repo="$1" source_file="$2" hook_name="$3" bot_user="$4"
  local hooks_dir dest relative exclude_file

  hooks_dir=$(repo_hooks_dir "$repo")
  mkdir -p "$hooks_dir"
  hooks_dir=$(cd "$hooks_dir" && pwd -P)
  dest="$hooks_dir/$hook_name"
  relative=$(repo_worktree_relative "$repo" "$dest")

  if [ -n "$relative" ] && git -C "$repo" ls-files --error-unmatch -- "$relative" >/dev/null 2>&1; then
    echo "  ⚠ $hook_name not installed: that repo tracks $relative as its own hook," >&2
    echo "    and overwriting a file it has committed is not ours to do." >&2
    return 1
  fi

  sed "s/__BOT_USERNAME__/$bot_user/g" "$source_file" >"$dest"
  chmod +x "$dest"

  # Where the hooks directory is inside the working tree, the file lands among version-controlled
  # ones and would otherwise show up as untracked. .git/info/exclude is local to the clone, so it
  # hides the file without editing a .gitignore that repo tracks.
  if [ -n "$relative" ]; then
    exclude_file=$(repo_abs_git_path "$repo" "$(git -C "$repo" rev-parse --git-path info/exclude)")
    mkdir -p "$(dirname "$exclude_file")"
    if ! grep -qxF "/$relative" "$exclude_file" 2>/dev/null; then
      printf '/%s\n' "$relative" >>"$exclude_file"
    fi
  fi
}
