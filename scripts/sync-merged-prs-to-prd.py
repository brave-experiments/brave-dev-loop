#!/usr/bin/env python3
"""Retire stories whose pull request has already been merged.

A story stays "pushed" until an agent iteration looks at its PR and finds it
merged. Nothing else closes that loop, so a PR a maintainer merges between runs
keeps its story in the pushed queue — and select-task.py ranks pushed
maintenance above pending development, so the next run spends a whole agent
iteration per merged PR to ask GitHub a question one API call answers. The run
that prompted this script spent eight of its ten iterations that way and opened
one new pull request.

This is the other end of sync-bot-prs-to-prd.py: that one turns the bot's open
PRs into "pushed" stories, this one retires them once they land.

Usage:
  scripts/sync-merged-prs-to-prd.py              # update data/prd.json in place
  scripts/sync-merged-prs-to-prd.py --dry-run    # report only, change nothing

Exit codes:
  0 - success (stories retired, or nothing to retire)
  2 - error
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

_script_dir = os.path.dirname(os.path.abspath(__file__))
_bot_dir = os.path.dirname(_script_dir)
sys.path.insert(0, _script_dir)
from lib.load_config import (  # noqa: E402
    load_config,
    require_config,
    resolve_target_repo,
)
from lib.prd_store import load_prd, prd_lock, save_prd  # noqa: E402

_config = load_config()
_pr_repo = require_config(_config, "project.prRepository")

PR_FIELDS = "state,mergedAt,headRefOid"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_git(args):
    """Run a git command, returning stdout, or None when it fails."""
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, timeout=120
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def fetch_pr(number):
    """State of one PR, or None when GitHub cannot be asked about it.

    A single unreachable PR must not cost the whole pass: this runs before a
    run's first iteration, and reconciling the other nineteen stories is still
    worth doing.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(number), "--repo", _pr_repo, "--json", PR_FIELDS],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  PR #{number}: could not be read ({e})", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(
            f"  PR #{number}: could not be read ({result.stderr.strip()})",
            file=sys.stderr,
        )
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"  PR #{number}: unreadable response ({e})", file=sys.stderr)
        return None


def pushed_pr_numbers(prd):
    """PR numbers of every story sitting in "pushed"."""
    numbers = []
    for story in prd.get("stories", []):
        if story.get("status") != "pushed":
            continue
        number = story.get("prNumber")
        if isinstance(number, int):
            numbers.append(number)
    return sorted(set(numbers))


def retire(story, merged_at):
    """Move one story from "pushed" to "merged".

    The fields match `update-prd-status.py merged` so a story retired here is
    indistinguishable from one an iteration retired. ``mergedAt`` is the real
    merge time rather than now, because it is a fact about the PR.
    """
    story["status"] = "merged"
    story["mergedAt"] = merged_at or now_iso()


def story_worktrees(repo, branches):
    """Map branch name -> worktree path, for the branches asked about.

    Worktrees are matched by the branch they have checked out, not by the
    `../<repo>-<issue>` path the profile documents: the convention lives in
    prose, and re-deriving it here would delete a directory chosen by a rule
    this script cannot see. The main checkout is never a candidate.
    """
    listing = run_git(["-C", repo, "worktree", "list", "--porcelain"])
    if listing is None:
        return {}
    main_path = os.path.realpath(repo)
    found = {}
    path = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/") :]
            if branch in branches and os.path.realpath(path) != main_path:
                found[branch] = path
    return found


def worktree_is_disposable(path, head_oid):
    """True when nothing would be lost by removing this worktree.

    Two conditions, both the ones an iteration checked by hand: the tree has no
    uncommitted changes, and its HEAD is the exact commit GitHub merged. A tree
    sitting on a later commit has work the PR never carried, so it stays.
    """
    status = run_git(["-C", path, "status", "--porcelain"])
    if status is None or status.strip():
        return False
    head = run_git(["-C", path, "rev-parse", "HEAD"])
    if head is None:
        return False
    return head.strip() == head_oid


def remove_worktree(repo, path):
    """Remove a worktree under the repo lock. True when it is gone."""
    lock = os.path.join(_script_dir, "git-repo-lock.sh")
    for command in (
        ["git", "-C", repo, "worktree", "remove", path],
        ["git", "-C", repo, "worktree", "prune"],
    ):
        result = subprocess.run(
            [lock, repo, "--"] + command, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            print(
                f"  worktree {path}: not removed ({result.stderr.strip()})",
                file=sys.stderr,
            )
            return False
    return True


def clean_up_worktrees(retired, dry_run):
    """Remove the worktrees of stories that were just retired.

    An iteration used to do this as the last step of the merged transition. No
    iteration will now, so a worktree left here is one nothing ever collects.
    """
    branches = {s["branch"]: s for s in retired if s.get("branch")}
    if not branches:
        return []
    repo = resolve_target_repo(_config)
    if not os.path.exists(os.path.join(repo, ".git")):
        return []
    removed = []
    for branch, path in story_worktrees(repo, set(branches)).items():
        if not worktree_is_disposable(path, branches[branch]["headRefOid"]):
            print(
                f"  worktree {path}: kept — uncommitted changes, or HEAD is not "
                f"the merged commit",
                file=sys.stderr,
            )
            continue
        if dry_run or remove_worktree(repo, path):
            removed.append(path)
    return removed


def main():
    parser = argparse.ArgumentParser(
        description="Retire pushed stories whose PR has already been merged"
    )
    parser.add_argument(
        "--prd",
        default=os.path.join(_bot_dir, "data", "prd.json"),
        help="Path to prd.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be retired, change nothing",
    )
    args = parser.parse_args()

    if not os.path.exists(args.prd):
        print(f"Error: {args.prd} does not exist", file=sys.stderr)
        return 2

    # The GitHub reads stay outside the lock, as in the sibling syncs: holding
    # the PRD across twenty network calls would stall every other run.
    candidates = pushed_pr_numbers(load_prd(args.prd))
    merged = {}
    for number in candidates:
        pr = fetch_pr(number)
        if pr and pr.get("state") == "MERGED":
            merged[number] = pr

    retired = []
    with prd_lock(args.prd):
        prd = load_prd(args.prd)
        for story in prd.get("stories", []):
            # Re-checked under the lock: another run's iteration may have
            # retired this story since the PR numbers were collected, and
            # rewriting it would undo whatever else that iteration recorded.
            if story.get("status") != "pushed":
                continue
            pr = merged.get(story.get("prNumber"))
            if not pr:
                continue
            retire(story, pr.get("mergedAt"))
            retired.append(
                {
                    "id": story["id"],
                    "prNumber": story["prNumber"],
                    "title": story.get("title"),
                    "branch": story.get("branchName"),
                    "headRefOid": pr.get("headRefOid"),
                }
            )
        if retired and not args.dry_run:
            save_prd(args.prd, prd)

    removed = clean_up_worktrees(retired, args.dry_run)

    verb = "Would retire" if args.dry_run else "Retired"
    print(
        f"{verb} {len(retired)} merged PR(s) of {len(candidates)} pushed story(ies)",
        file=sys.stderr,
    )
    for story in retired:
        print(f"  {story['id']}: PR #{story['prNumber']} merged", file=sys.stderr)

    print(
        json.dumps(
            {
                "retired": retired,
                "checked": len(candidates),
                "worktreesRemoved": removed,
                "dryRun": args.dry_run,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
