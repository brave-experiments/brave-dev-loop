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
#
# A *relative* core.hooksPath adds a third hazard, and it is the one that let an unsigned commit
# reach a pull request. Git resolves it against each working tree's own top level, so a story's
# worktree looks for hooks in its own copy of that directory -- which holds only what the repo
# tracks, since `git worktree add` writes no untracked file. Every hook installed here is untracked
# by design, so a worktree pushed with exactly the checks the repo committed and none of ours. The
# signature guard was the one that mattered: brave/bravebot#641 was pushed unsigned from a worktree
# while the same push from the main checkout would have been blocked.
#
# post-checkout hid it. Git resolves that hook from the checkout being *left* and runs it with the
# new worktree as its cwd, so it fires on `git worktree add` and appears to prove the hooks are in
# force there. pre-push is resolved from the tree being pushed, and is not.
#
# So the path is anchored: made absolute in the repo's config, pointing at the main checkout's
# directory, which is the one setup wrote into. See repo_anchor_hooks_path.

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

# The hooks a target repo gets, in one place, so a hook added to this checkout reaches both the
# machine being set up and every run after that.
REPO_BOT_HOOKS="pre-commit pre-push post-checkout"

# Whether the hook a repo has installed is already what this checkout would write. Compared against
# the rendered source rather than by date, so an edit here counts and a re-run does not.
repo_hook_current() {
  local repo="$1" source_file="$2" hook_name="$3" bot_user="$4" dest
  dest="$(repo_hooks_dir "$repo")/$hook_name"
  [ -f "$dest" ] || return 1
  sed "s/__BOT_USERNAME__/$bot_user/g" "$source_file" | cmp -s - "$dest"
}

# Echo the path a repo tracks its own hook of this name at, or nothing where it tracks none. Read
# only: it looks where git looks without creating the directory, since a repo tracking a file there
# has one already.
repo_tracked_hook() {
  local repo="$1" hook_name="$2" dir dest relative
  dir=$(repo_hooks_dir "$repo")
  [ -d "$dir" ] || return 0
  dest="$(cd "$dir" && pwd -P)/$hook_name"
  relative=$(repo_worktree_relative "$repo" "$dest")
  [ -n "$relative" ] || return 0
  git -C "$repo" ls-files --error-unmatch -- "$relative" >/dev/null 2>&1 || return 0
  printf '%s\n' "$relative"
}

# Say which hooks a repo keeps its own committed copy of, so whoever is setting the machine up knows
# the guard that one would have carried is not in force. Setup's business rather than a run's: it is
# an arrangement somebody chose, and a warning repeated at every start is one nobody reads.
repo_report_tracked_hooks() {
  local repo="$1" name tracked
  for name in $REPO_BOT_HOOKS; do
    tracked=$(repo_tracked_hook "$repo" "$name")
    [ -n "$tracked" ] || continue
    echo "  ⚠ $name not installed: that repo tracks $tracked as its own hook."
  done
  return 0
}

# Bring a target repo's hooks up to date with this checkout's, saying nothing about the ones that
# already are.
#
# `make setup` installs them, and it runs once per machine. So a hook added here afterwards reaches a
# repo configured before it existed only if somebody remembers to run setup again, and until they do
# nothing reports the gap: the repo pushes exactly as it always did, with one fewer check than this
# checkout believes it has. The signature check landed that way and sat uninstalled for a week in
# the repository it was written for.
repo_refresh_bot_hooks() {
  local repo="$1" bot_user="$2" hooks_src="$3" name note
  [ -n "$bot_user" ] || return 0
  for name in $REPO_BOT_HOOKS; do
    [ -f "$hooks_src/$name" ] || continue
    [ -n "$(repo_tracked_hook "$repo" "$name")" ] && continue
    repo_hook_current "$repo" "$hooks_src/$name" "$name" "$bot_user" && continue
    case "$name" in
      pre-commit) note="blocks $bot_user from modifying dependencies" ;;
      pre-push) note="blocks a push whose commits are not $bot_user's, or unsigned" ;;
      post-checkout) note="gives a new worktree the main checkout's .envrc and hooks" ;;
    esac
    if repo_install_hook "$repo" "$hooks_src/$name" "$name" "$bot_user"; then
      echo "  ✓ $name hook installed ($note)"
    fi
  done

  repo_refresh_worktree_hooks "$repo" "$bot_user" "$hooks_src"
  return 0
}

# The same hooks in every existing worktree, where a relative core.hooksPath gives each its own
# directory to look in.
#
# hooks/post-checkout covers a worktree being created. It cannot cover one that already exists: it
# fires on the checkout that makes a worktree and never again, so a worktree added before this
# landed -- or before the hook it is missing was written -- keeps pushing without it. Stories are
# long-lived and their worktrees are re-entered across runs, so that is the common case rather than
# an edge one, and it is the case brave/bravebot#641 was pushed from.
#
# Only for a relative path. An absolute one, or an unset one, already resolves to a single directory
# for the whole repo, so installing into the main checkout was enough.
repo_refresh_worktree_hooks() {
  local repo="$1" bot_user="$2" hooks_src="$3" configured worktree name
  configured=$(git -C "$repo" config --path core.hooksPath 2>/dev/null || true)
  [ -n "$configured" ] || return 0
  case "$configured" in
    /* | ../*) return 0 ;;
  esac

  while read -r worktree; do
    [ -n "$worktree" ] || continue
    [ -d "$worktree" ] || continue
    # The main checkout is what the caller just did.
    [ "$(git -C "$worktree" rev-parse --git-dir)" = "$(git -C "$worktree" rev-parse --git-common-dir)" ] && continue
    for name in $REPO_BOT_HOOKS; do
      [ -f "$hooks_src/$name" ] || continue
      [ -n "$(repo_tracked_hook "$worktree" "$name")" ] && continue
      repo_hook_current "$worktree" "$hooks_src/$name" "$name" "$bot_user" && continue
      if repo_install_hook "$worktree" "$hooks_src/$name" "$name" "$bot_user"; then
        echo "  ✓ $name hook installed in $(basename "$worktree")"
      fi
    done
  done <<<"$(git -C "$repo" worktree list --porcelain | sed -n 's/^worktree //p')"
  return 0
}
