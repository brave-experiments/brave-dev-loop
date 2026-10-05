#!/usr/bin/env python3
"""Approve a pull request as the bot's owner, and optionally squash-merge it.

    scripts/merge-pr.py approve https://github.com/brave/bravebot/pull/351
    scripts/merge-pr.py merge 351

`make approve PR=<url|number>` and `make merge PR=<url|number>` are the intended
entry points.

Both act as `project.botOwnerGithubHandle`, never as the bot: the bot does not
approve its own pull request and never merges one, and a person's approval is the
one that counts. The owner's token is read with `gh auth token --user`, which
leaves gh's active account alone, from a gh environment stripped of the bot's
`GH_TOKEN` and `GH_CONFIG_DIR` -- otherwise a shell `run.sh` set up would make
every call below the bot's. The login is checked before anything is written.

`merge` approves first (unless the owner opened it, which GitHub will not let them
approve) and then squash-merges, pinned to the commit that was
approved so a push that lands in between fails the merge instead of merging code
nobody saw. Squash is the project's merge method. Nothing is forced: a pull
request GitHub will not merge yet -- required checks pending, a conflict -- is
reported as GitHub words it, and the approval stays.

No model is involved: gh decides everything here.
"""

import argparse
import json
import os

# gh only, with an argument list and no shell; the values reaching it are the
# repository and number parsed from the argument and the config's owner handle.
import subprocess  # nosemgrep
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.load_config import get_config, load_config
from lib.pr_worktree import ask_for_pr, die, parse_pr, say

# Each of these outranks the stored account, so any one left in would make the
# calls below someone else's.
BOT_IDENTITY_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_CONFIG_DIR")


def gh(*args, env=None):
    """Run gh, returning stdout; a failure stops with gh's own words."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        die(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def owner_env(handle):
    """The environment that makes gh act as `handle`, checked before it is used."""
    env = {k: v for k, v in os.environ.items() if k not in BOT_IDENTITY_VARS}
    token = subprocess.run(
        ["gh", "auth", "token", "--user", handle],
        capture_output=True,
        text=True,
        env=env,
    )
    if token.returncode != 0 or not token.stdout.strip():
        die(
            f"gh has no stored login for {handle} "
            f"(`gh auth login` as {handle} adds one): {token.stderr.strip()}"
        )
    env["GH_TOKEN"] = token.stdout.strip()
    login = gh("api", "user", "--jq", ".login", env=env)
    if login.lower() != handle.lower():
        die(
            f"gh resolved to {login}, not {handle}; refusing to act as the wrong account"
        )
    return env


def main_entry():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["approve", "merge"])
    parser.add_argument("pr", nargs="?", help="pull request URL or number")
    parser.add_argument("--repo", help="owner/name, for a bare pull request number")
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="fail instead of asking when no pull request is given",
    )
    args = parser.parse_args()

    config = load_config()
    handle = get_config(config, "project.botOwnerGithubHandle")
    if not handle:
        die("project.botOwnerGithubHandle is not set in config.json")

    ref = args.pr or (None if args.no_input else ask_for_pr())
    if not ref:
        die("no pull request given: PR=<url|number>")
    repo, number = parse_pr(
        ref, args.repo or get_config(config, "project.prRepository")
    )

    env = owner_env(handle)
    pr = json.loads(
        gh(
            "pr", "view", number, "--repo", repo,
            "--json", "state,isDraft,title,headRefOid,author",
            env=env,
        )
    )  # fmt: skip
    say(f"{repo}#{number} [{pr['state']}] {pr['title']}")
    if pr["state"] != "OPEN":
        die(f"{repo}#{number} is {pr['state'].lower()}; nothing to {args.action}")
    if pr["isDraft"] and args.action == "merge":
        die(f"{repo}#{number} is a draft; mark it ready for review first")
    if pr["author"]["login"].lower() == handle.lower():
        if args.action == "approve":
            die(f"{handle} opened {repo}#{number}; GitHub rejects self-approval")
        say(f"  {handle} opened it, so there is no approval to give")
    else:
        gh("pr", "review", number, "--repo", repo, "--approve", env=env)
        say(f"  approved as {handle}")
    if args.action == "approve":
        return

    gh(
        "pr", "merge", number, "--repo", repo, "--squash",
        "--match-head-commit", pr["headRefOid"],
        env=env,
    )  # fmt: skip
    say(f"  squash-merged {repo}#{number}")


if __name__ == "__main__":
    main_entry()
