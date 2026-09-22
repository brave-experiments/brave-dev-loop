#!/usr/bin/env python3
"""Print the git worktree for a pull request, creating it when there is none.

Prints the worktree's absolute path on stdout and nothing else, so a caller can
cd into it:

    cd "$(scripts/worktree-for-pr.py https://github.com/brave/bravebot/pull/351)"

`make worktree PR=<url>` is the intended entry point -- a child process cannot
change its parent's directory, so the Makefile does the cd and opens a shell.

Progress, warnings and the prompt for a missing pull request all go to the
terminal rather than stdout, so capturing stdout still leaves the user informed.
Nothing here commits or pushes; `scripts/rebase-pr.py` is the one that does.

The resolution itself lives in lib/pr_worktree.py, shared with that script. No
model is involved: gh and git decide everything.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.load_config import bot_dir, load_config
from lib.pr_worktree import main_checkout, read_pr, say, worktree_for


def warn_if_behind(dest, head_oid):
    """Say so when the worktree is not on the pull request's head commit.

    A reused worktree is wherever its last session left it -- rebased, ahead by
    a fix that was never pushed, or behind a force-push -- and that difference
    decides whether the tree you are about to read is what the pull request says.
    """
    head = subprocess.run(
        ["git", "-C", dest, "rev-parse", "HEAD"], capture_output=True, text=True
    )
    if head.returncode != 0:
        return
    local = head.stdout.strip()
    if local == head_oid:
        return
    say(
        f"  Warning: HEAD is {local[:9]} but the pull request is at "
        f"{head_oid[:9]} -- this tree is not what the pull request shows."
    )


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
    warn_if_behind(dest, pr["headRefOid"])
    print(dest)


if __name__ == "__main__":
    main_entry()
