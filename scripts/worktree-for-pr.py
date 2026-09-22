#!/usr/bin/env python3
"""Print the git worktree for a pull request, creating it when there is none.

Prints the worktree's absolute path on stdout and nothing else, so a caller can
cd into it:

    cd "$(scripts/worktree-for-pr.py https://github.com/brave/bravebot/pull/351)"

`make worktree PR=<url>` is the intended entry point -- a child process cannot
change its parent's directory, so the Makefile does the cd and opens a shell.

The tree comes back on the pull request's head where getting there is a
fast-forward, so what is read is what the pull request says. Progress, warnings
and the prompt for a missing pull request all go to the terminal rather than
stdout, so capturing stdout still leaves the user informed. Nothing here commits
or pushes; `scripts/rebase-pr.py` is the one that does.

The resolution itself lives in lib/pr_worktree.py, shared with that script. No
model is involved: gh and git decide everything.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.load_config import bot_dir, load_config
from lib.pr_worktree import (
    align_with_remote,
    fetch_head,
    have_commit,
    main_checkout,
    read_pr,
    say,
    uncommitted,
    worktree_for,
)


def catch_up(bot, main, dest, repo, number, pr):
    """Move the worktree onto the pull request's head, or say why it is not there.

    A worktree is wherever its last session left it, and a local branch is
    wherever it was last fetched -- which for a branch pushed from another
    machine, or by the bot on its next iteration, is behind the pull request and
    sometimes behind the whole change. Landing there means reading a tree that
    says something the pull request does not. Creation has the same hole from the
    other side: `worktree add <dest> <branch>` uses the local branch wherever it
    was left, so a fresh directory can start out stale too.

    A fast-forward of a clean tree is the only move taken, because it is the only
    one that cannot lose work. The two cases it leaves alone -- uncommitted
    changes, and a commit the head does not have -- are also what makes this safe
    against a worktree a bot run is live in: mid-story such a tree is one or the
    other. `make rebase` refuses outright in both; this still has to open a
    shell, so it says so and opens it where the tree already is.
    """
    head = pr["headRefOid"]
    branch = pr["headRefName"]
    owner = pr["headRepositoryOwner"]["login"]

    if not have_commit(main, head):
        fetch_head(bot, main, repo, number, branch, owner)
    if not have_commit(main, head):
        say(
            f"  Warning: the pull request is at {head[:9]}, which is not in this "
            "repository -- leaving the tree where it is."
        )
        return

    if uncommitted(dest):
        say(
            f"  Warning: this tree has uncommitted changes, so it stays where it "
            f"is rather than moving to the pull request's {head[:9]}."
        )
        return

    def warn(local, remote_head):
        say(
            f"  Warning: HEAD is {local[:9]}, which the pull request's "
            f"{remote_head[:9]} does not have -- this tree is not what the pull "
            "request shows, and moving it would drop that commit."
        )

    align_with_remote(dest, branch, head, warn)


def main_entry():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", nargs="?", help="pull request URL or number")
    parser.add_argument("--main", help="the main checkout (default: from config.json)")
    parser.add_argument("--repo", help="owner/name, for a bare pull request number")
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="fail instead of asking when no pull request is given",
    )
    args = parser.parse_args()

    config = load_config()
    bot = bot_dir()
    main = main_checkout(config, args.main)

    repo, number, pr = read_pr(
        args.pr, config, args.repo, allow_input=not args.no_input
    )
    say(f"{repo}#{number} [{pr['state']}] {pr['title']}")
    say(f"  branch {pr['headRefName']}")

    dest = worktree_for(bot, main, repo, number, pr)
    catch_up(bot, main, dest, repo, number, pr)
    print(dest)


if __name__ == "__main__":
    main_entry()
