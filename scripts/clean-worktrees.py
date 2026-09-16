#!/usr/bin/env python3
"""Remove the story worktrees nothing is using any more.

A worktree profile gives every story its own `../<repo>-<issue>` checkout, and
each one grows a full build directory — 3 to 4 GB for a Rust project. The only
thing that ever collected them was sync-merged-prs-to-prd.py, which removes a
worktree in the same pass that retires its story. Every other ending leaves one
behind for good: a story that was skipped, a run that was killed, a PR merged
before that sync existed. Twenty-seven had piled up in the directory this was
written for, a hundred gigabytes of build output for work that had all landed.

What it will not touch:
  - the main checkout
  - a worktree a live run claims (by its branch, or by the `-<issue>` in its path)
  - a worktree git has locked
  - anything holding work no remote has: uncommitted changes, or commits that
    exist on no remote-tracking branch
  - with --max-age-hours, anything added more recently than that

Removal renames the directory, prunes the administrative entries under the
repository lock, and deletes the contents afterwards. Deleting 4 GB takes long
enough that doing it under the lock would stall every other run's fetch.

Usage:
  scripts/clean-worktrees.py                      # every worktree nothing is using
  scripts/clean-worktrees.py --max-age-hours 24   # only ones added over a day ago
  scripts/clean-worktrees.py --dry-run            # report only, delete nothing

Exit codes:
  0 - success (worktrees removed, or nothing to remove)
  2 - error
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

_script_dir = os.path.dirname(os.path.abspath(__file__))
_bot_dir = os.path.dirname(_script_dir)
sys.path.insert(0, _script_dir)
from lib import claims as claims_lib  # noqa: E402
from lib.load_config import load_config, resolve_target_repo  # noqa: E402
from lib.prd_store import bot_dir_for, load_prd  # noqa: E402

# Renamed to this before deletion, so the slow part happens outside the lock.
TRASH_SUFFIX = ".deleting"


def run_git(args, timeout=120):
    """Run a git command, returning stdout, or None when it fails."""
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, timeout=timeout
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def list_worktrees(repo):
    """Every linked worktree of ``repo``, main checkout excluded.

    ``prunable`` is carried through so a worktree whose directory somebody
    deleted by hand still gets its administrative entry collected.
    """
    listing = run_git(["-C", repo, "worktree", "list", "--porcelain"])
    if listing is None:
        return []
    main = os.path.realpath(repo)
    trees = []
    current = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current = {
                "path": line[len("worktree ") :],
                "branch": None,
                "locked": False,
                "prunable": False,
            }
            trees.append(current)
        elif current is None:
            continue
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/") :]
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = True
        elif line == "prunable" or line.startswith("prunable "):
            current["prunable"] = True
    return [t for t in trees if os.path.realpath(t["path"]) != main]


def age_hours(path):
    """Hours since the worktree was added, or None when it cannot be told.

    The `.git` file inside a worktree is written by `git worktree add` and never
    touched again, so its mtime is the moment the worktree appeared. The
    directory's own mtime is not: every build writes into it.
    """
    try:
        return (time.time() - os.path.getmtime(os.path.join(path, ".git"))) / 3600.0
    except OSError:
        return None


def local_work_reason(path):
    """What removing this worktree would lose, or None when nothing would be.

    Two things can be lost: changes that were never committed, and commits that
    exist on no remote. The second is what makes this safe to run while a story
    is mid-development — a branch the bot has committed to but not pushed has no
    remote-tracking ref containing its HEAD, so it is kept.

    A reason is also returned when git cannot answer, so a worktree in a state
    this does not understand stays where it is.
    """
    status = run_git(["-C", path, "status", "--porcelain"])
    if status is None:
        return "git could not read its status"
    if status.strip():
        return "it has uncommitted changes"
    on_remote = run_git(
        ["-C", path, "for-each-ref", "--contains", "HEAD", "--count=1", "refs/remotes/"]
    )
    if on_remote is None:
        return "git could not say whether its HEAD is on a remote"
    if not on_remote.strip():
        return "its HEAD is on no remote branch"
    return None


def issue_number(story):
    """Issue number a story references in its description, or None."""
    match = re.search(r"issue #(\d+)", story.get("description") or "")
    return int(match.group(1)) if match else None


def claimed_worktrees(repo, bot_dir, prd_path):
    """(branches, paths) that a live run is working in.

    A claim names a story, not a directory, so the two are joined through the
    PRD — by the branch the story recorded, and by the issue number in its
    description, which is what the `<repo>-<issue>` directory is named after.
    Both, because a story mid-development has a worktree before it has a branch
    name in the PRD.
    """
    claimed = set(claims_lib.active(bot_dir))
    if not claimed:
        return set(), set()
    try:
        prd = load_prd(prd_path)
    except (json.JSONDecodeError, OSError):
        # Without the PRD there is no way to tell which worktree belongs to a
        # claimed story, so treat every one as in use.
        return set(), None
    parent = os.path.dirname(os.path.realpath(repo))
    prefix = os.path.basename(os.path.realpath(repo))
    branches = set()
    paths = set()
    for story in prd.get("stories", []):
        if story.get("id") not in claimed:
            continue
        if story.get("branchName"):
            branches.add(story["branchName"])
        issue = issue_number(story)
        if issue is not None:
            paths.add(os.path.join(parent, f"{prefix}-{issue}"))
    return branches, paths


def sweep_leftovers(repo):
    """Delete what a previous run renamed but was killed before deleting."""
    parent = os.path.dirname(os.path.realpath(repo))
    swept = []
    try:
        entries = sorted(os.listdir(parent))
    except OSError:
        return swept
    for name in entries:
        if not name.endswith(TRASH_SUFFIX):
            continue
        path = os.path.join(parent, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            swept.append(path)
    return swept


def prune(repo):
    """Collect the administrative entries of worktrees that are gone."""
    lock = os.path.join(_script_dir, "git-repo-lock.sh")
    result = subprocess.run(
        [lock, repo, "--timeout", "120", "--", "git", "-C", repo, "worktree", "prune"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(f"  worktree prune failed ({result.stderr.strip()})", file=sys.stderr)
        return False
    return True


def select(repo, bot_dir, prd_path, max_age_hours):
    """Split the worktrees into the ones to remove and why the rest are kept."""
    branches, paths = claimed_worktrees(repo, bot_dir, prd_path)
    remove = []
    kept = []
    prunable = False
    for tree in list_worktrees(repo):
        path = tree["path"]
        if tree["prunable"]:
            prunable = True
            continue
        if tree["locked"]:
            kept.append({"path": path, "reason": "git has it locked"})
            continue
        if paths is None or path in paths or os.path.realpath(path) in paths:
            kept.append({"path": path, "reason": "a live run is working in it"})
            continue
        if tree["branch"] and tree["branch"] in branches:
            kept.append({"path": path, "reason": "a live run holds its story"})
            continue
        if max_age_hours is not None:
            age = age_hours(path)
            if age is None or age < max_age_hours:
                kept.append({"path": path, "reason": "it was added too recently"})
                continue
        reason = local_work_reason(path)
        if reason:
            kept.append({"path": path, "reason": reason})
            continue
        remove.append(path)
    return remove, kept, prunable


def remove_worktrees(paths, dry_run):
    """Rename each directory aside. Returns [(worktree path, renamed path)]."""
    renamed = []
    for path in paths:
        trash = path.rstrip(os.sep) + TRASH_SUFFIX
        if dry_run:
            renamed.append((path, None))
            continue
        try:
            os.rename(path, trash)
        except OSError as e:
            print(f"  {path}: kept — could not be moved aside ({e})", file=sys.stderr)
            continue
        renamed.append((path, trash))
    return renamed


def main():
    parser = argparse.ArgumentParser(
        description="Remove the story worktrees nothing is using any more"
    )
    parser.add_argument("--repo", help="Target repository (default: from config.json)")
    parser.add_argument(
        "--prd",
        default=os.path.join(_bot_dir, "data", "prd.json"),
        help="Path to prd.json (read to map live claims onto worktrees)",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        help="Only remove worktrees added more than this many hours ago",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would go, delete nothing"
    )
    args = parser.parse_args()

    repo = args.repo or resolve_target_repo(load_config())
    if not os.path.exists(os.path.join(repo, ".git")):
        print(f"Error: '{repo}' is not a git repository", file=sys.stderr)
        return 2

    swept = [] if args.dry_run else sweep_leftovers(repo)
    remove, kept, prunable = select(
        repo, bot_dir_for(args.prd, _bot_dir), args.prd, args.max_age_hours
    )
    renamed = remove_worktrees(remove, args.dry_run)

    if (renamed or prunable) and not args.dry_run:
        prune(repo)
    for _, trash in renamed:
        if trash:
            shutil.rmtree(trash, ignore_errors=True)

    verb = "Would remove" if args.dry_run else "Removed"
    print(f"{verb} {len(renamed)} worktree(s), kept {len(kept)}", file=sys.stderr)
    for path, _ in renamed:
        print(f"  {path}: removed", file=sys.stderr)
    for tree in kept:
        print(f"  {tree['path']}: kept — {tree['reason']}", file=sys.stderr)

    print(
        json.dumps(
            {
                "removed": [path for path, _ in renamed],
                "kept": kept,
                "swept": swept,
                "dryRun": args.dry_run,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
