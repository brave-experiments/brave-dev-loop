#!/usr/bin/env python3
"""Remove the story worktrees nothing is using any more.

A worktree profile gives every story its own `../<repo>-<issue>` checkout, and
each one grows a full build directory — 3 to 4 GB for a Rust project. The only
thing that ever collected them was sync-merged-prs-to-prd.py, which removes a
worktree in the same pass that retires its story. Every other ending leaves one
behind for good: a story that was skipped, a run that was killed, a PR merged
before that sync existed. Twenty-seven had piled up in the directory this was
written for, a hundred gigabytes of build output for work that had all landed.

A worktree's branch belongs to the repository, not to the worktree checked out on
it, so removal leaves the branch where it was and `git worktree add <path>
<branch>` brings the work back. Uncommitted changes are the only thing a worktree
holds alone, and so the only thing removing one can lose.

What it will not touch:
  - the main checkout
  - a worktree a live run claims (by its branch, or by the `-<issue>` in its path)
  - a worktree git has locked
  - a worktree with uncommitted changes, or a detached one whose commits no ref
    keeps
  - with --max-age-hours, anything used more recently than that

--all drops the fourth and fifth of those, for the operator emptying a directory
rather than a run collecting after itself: every worktree goes, however recently
it was used and whatever it holds. A live run's worktree and a locked one are
kept regardless, because deleting the directory out from under a running session
breaks it.

Removal renames the directory, prunes the administrative entries under the
repository lock, and deletes the contents afterwards. Deleting 4 GB takes long
enough that doing it under the lock would stall every other run's fetch.

Usage:
  scripts/clean-worktrees.py                      # every worktree nothing is using
  scripts/clean-worktrees.py --max-age-hours 24   # only ones idle over a day
  scripts/clean-worktrees.py --all                # every one, work and all
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


def admin_dir(path):
    """The repository's administrative directory for this worktree, or None.

    A worktree's `.git` is a file holding `gitdir: <path>`, which points at the
    `.git/worktrees/<name>` directory the repository keeps for it.
    """
    try:
        with open(os.path.join(path, ".git")) as f:
            content = f.read()
    except OSError:
        return None
    for line in content.splitlines():
        if line.startswith("gitdir:"):
            return line[len("gitdir:") :].strip()
    return None


def idle_hours(path):
    """Hours since git last did anything in this worktree, or None if unknowable.

    Work in a worktree writes files into that worktree's administrative
    directory — the index on a commit, HEAD on a checkout, FETCH_HEAD on a fetch
    — so the newest mtime among them is when it was last worked in. The `.git`
    file is written once by `git worktree add`, and covers one nothing has run
    git in yet. The worktree directory's own mtime is neither: a build writes
    deep inside it without touching the top level.

    Add time alone is not enough. A long-lived story's worktree is days old and
    still in use, and between two iterations of it there is a moment — pushed,
    nothing uncommitted, no claim held — when every other check would let it go.

    Only files count, never the administrative directory itself. Every git
    command creates a lock file in there and deletes it again, which bumps the
    directory's mtime, so counting it would have this script's own reads make
    each worktree look busy and nothing would ever be collected again.
    """
    marker = os.path.join(path, ".git")
    try:
        newest = os.path.getmtime(marker)
    except OSError:
        return None
    admin = admin_dir(path)
    try:
        entries = list(os.scandir(admin)) if admin else []
    except OSError:
        entries = []
    for entry in entries:
        try:
            if entry.is_file():
                newest = max(newest, entry.stat().st_mtime)
        except OSError:
            continue
    return (time.time() - newest) / 3600.0


def local_work_reason(path, branch):
    """What removing this worktree would lose, or None when nothing would be.

    Uncommitted changes are the one thing that goes. A branch is not: it is the
    repository's, so the commits stay reachable under the same name and the
    story's next iteration checks it out again.

    Whether a remote has those commits is therefore not asked. It used to be,
    and it meant no finished worktree was ever collected: the target
    squash-merges, so a branch's own commits never become ancestors of the base
    branch, and the head branch is deleted when the pull request merges. After
    that nothing under refs/remotes/ contains HEAD, ever again — so a story that
    landed months ago read exactly like one committed five minutes ago and every
    worktree accumulated for good, which is the pile this script exists to stop.

    A detached worktree is the case where commits really do go, since no branch
    names them: once the administrative entry is pruned, the reflog and gc are
    all that stand between them and collection.

    A reason is also returned when git cannot answer, so a worktree in a state
    this does not understand stays where it is.

    --no-optional-locks keeps the question from changing the answer: a plain
    `git status` refreshes the index when a build has touched the tree, and that
    write is indistinguishable from work when idle_hours() next reads it.
    """
    status = run_git(["--no-optional-locks", "-C", path, "status", "--porcelain"])
    if status is None:
        return "git could not read its status"
    if status.strip():
        return "it has uncommitted changes"
    if branch:
        return None
    kept_by = run_git(["-C", path, "for-each-ref", "--contains", "HEAD", "--count=1"])
    if kept_by is None:
        return "git could not say whether any ref keeps its commits"
    if not kept_by.strip():
        return "it is detached and no ref keeps its commits"
    return None


def unpushed_branch(path, branch):
    """The branch keeping this worktree's commits when no remote has them.

    Said as the worktree goes. Removal is silent about what it took, and a story
    committed but never pushed leaves the only copy of that work on a branch
    nobody is looking at; naming it is the difference between work parked and
    work presumed pushed.
    """
    if not branch:
        return None
    on_remote = run_git(
        ["-C", path, "for-each-ref", "--contains", "HEAD", "--count=1", "refs/remotes/"]
    )
    if on_remote is None or on_remote.strip():
        return None
    return branch


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


def select(repo, bot_dir, prd_path, max_age_hours, all_worktrees=False):
    """Split the worktrees into the ones to remove and why the rest are kept.

    Returns (remove, kept, prunable, lost, unpushed) — `lost` naming the
    worktrees that hold work `--all` is about to take, so the operator sees what
    went, and `unpushed` the ones whose commits no remote has. Those go either
    way, because their branch keeps the commits; they are named rather than kept.
    """
    branches, paths = claimed_worktrees(repo, bot_dir, prd_path)
    remove = []
    kept = []
    lost = []
    unpushed = []
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
        if max_age_hours is not None and not all_worktrees:
            idle = idle_hours(path)
            if idle is None or idle < max_age_hours:
                kept.append({"path": path, "reason": "it was used too recently"})
                continue
        reason = local_work_reason(path, tree["branch"])
        if reason:
            if not all_worktrees:
                kept.append({"path": path, "reason": reason})
                continue
            lost.append({"path": path, "reason": reason, "branch": tree["branch"]})
        else:
            parked = unpushed_branch(path, tree["branch"])
            if parked:
                unpushed.append({"path": path, "branch": parked})
        remove.append(path)
    return remove, kept, prunable, lost, unpushed


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
        help="Only remove worktrees not used for more than this many hours",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Remove every worktree, however recently used and whatever it holds "
        "(uncommitted changes are lost; branches and their commits are not)",
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
    remove, kept, prunable, lost, unpushed = select(
        repo,
        bot_dir_for(args.prd, _bot_dir),
        args.prd,
        args.max_age_hours,
        all_worktrees=args.all,
    )
    renamed = remove_worktrees(remove, args.dry_run)

    if (renamed or prunable) and not args.dry_run:
        prune(repo)
    for _, trash in renamed:
        if trash:
            shutil.rmtree(trash, ignore_errors=True)

    verb = "Would remove" if args.dry_run else "Removed"
    print(f"{verb} {len(renamed)} worktree(s), kept {len(kept)}", file=sys.stderr)
    gone = [path for path, _ in renamed]
    held = {tree["path"]: tree for tree in lost}
    parked = {tree["path"]: tree["branch"] for tree in unpushed}
    for path in gone:
        note = ""
        if path in held:
            branch = held[path]["branch"]
            where = (
                f"branch {branch} kept"
                if branch
                else "detached, nothing points at its commits"
            )
            note = f" — {held[path]['reason']} ({where})"
        elif path in parked:
            note = f" — no remote has its commits; branch {parked[path]} keeps them"
        print(f"  {path}: removed{note}", file=sys.stderr)
    for tree in kept:
        print(f"  {tree['path']}: kept — {tree['reason']}", file=sys.stderr)

    print(
        json.dumps(
            {
                "removed": gone,
                "kept": kept,
                "lostWork": [tree for tree in lost if tree["path"] in gone],
                "unpushed": [tree for tree in unpushed if tree["path"] in gone],
                "swept": swept,
                "dryRun": args.dry_run,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
