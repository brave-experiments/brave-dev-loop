#!/usr/bin/env python3
"""Rebase a pull request onto the branch it will be merged into, and push it.

    scripts/rebase-pr.py https://github.com/brave/bravebot/pull/351
    scripts/rebase-pr.py 351

`make rebase PR=<url>` is the intended entry point.

The rebase happens in the worktree that holds the branch -- the same one
`make worktree` opens, found by branch and created from the pull request's head
when none has it. The main checkout is left on the branch it is already on:
several runs share it, it is the one place holding the default branch, and a
worktree cannot check out a branch the main checkout has moved onto.

The base is the branch a reviewer merges into, which is not always where the
pull request was pushed. Where the project works through a fork, `upstream` is
the pull request's repository and `origin` is the fork; the rebase is onto
`upstream/<project.defaultBranch>` and the push goes back to the fork. Where
there is no fork the two are one remote and both halves use it.

Nothing is force-pushed until the rebase has succeeded, and the push carries a
lease on the commit the branch was fetched at, so a rebase racing someone else's
push is rejected rather than overwriting them.

No model is involved: gh and git decide everything here.
"""

import argparse
import os

# Only git, with argument lists and no shell. Two calls are deliberately not
# captured -- a rebase and a push report progress to the terminal as they go.
import subprocess  # nosemgrep
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.load_config import bot_dir, get_config, load_config
from lib.pr_worktree import (
    die,
    locked,
    main_checkout,
    read_pr,
    remote_for_owner,
    remotes,
    run,
    say,
    worktree_for,
)


def streamed(*args):
    """Run a command with its output left on the terminal, returning its code.

    A rebase and a push both report as they go, and it is the conflict message
    that names the files, which is what the reader has to act on. Every caller
    passes git an argument list this module built, with no shell anywhere.
    """
    return subprocess.run(args).returncode  # nosemgrep


def base_remote(main, repo):
    """The remote whose default branch a reviewer merges this pull request into.

    A literal `upstream` is that remote wherever the project works through a
    fork, and is preferred over matching the pull request repository's owner:
    a renamed repository (brave/bravebot, whose remote URL still says
    brave-experiments/bravebot) matches no owner at all.
    """
    if "upstream" in remotes(main):
        return "upstream"
    return remote_for_owner(main, repo.partition("/")[0]) or "origin"


def is_ancestor(work, earlier, later):
    return (
        subprocess.run(
            ["git", "-C", work, "merge-base", "--is-ancestor", earlier, later],
            capture_output=True,
        ).returncode
        == 0
    )


def require_clean(work):
    """Refuse to move a tree with uncommitted changes to tracked files in it.

    A rebase of a dirty tree stops halfway through anyway, and this worktree may
    be a story's: a session could be mid-edit in it right now. Untracked files
    are not counted -- a build directory and a screenshot are what a worktree is
    full of, and a rebase only minds one it would have to overwrite.
    """
    if run("git", "-C", work, "status", "--porcelain", "-uno"):
        die(f"{work} has uncommitted changes -- commit them before rebasing")


def align_with_remote(work, branch, remote_head):
    """Put the worktree on the commit the pull request's branch was fetched at.

    A worktree is wherever its last session left it. Behind the branch -- every
    commit in it already contained in `remote_head` -- is a fast-forward and
    loses nothing. Ahead of it is different: force-pushing a rebase of commits
    the pull request has never had would add work to it under the name of a
    rebase, so stop and let whoever made them decide.
    """
    local = run("git", "-C", work, "rev-parse", "HEAD")
    if local == remote_head:
        return
    if not is_ancestor(work, local, remote_head):
        die(
            f"{work} is at {local[:9]} and {branch} was pushed at "
            f"{remote_head[:9]}, so this tree holds commits the pull request "
            f"does not. Push or drop them before rebasing."
        )
    say(f"  fast-forwarding {local[:9]} -> {remote_head[:9]}")
    run("git", "-C", work, "reset", "--hard", remote_head)


def rebase(work, base):
    """Rebase onto `base`, aborting rather than leaving a half-done one behind.

    An abandoned rebase would strand the next session that uses this worktree,
    so it is cleaned up here and finished by hand afterwards.
    """
    say(f"  rebasing onto {base}")
    if streamed("git", "-C", work, "rebase", base) == 0:
        return
    subprocess.run(["git", "-C", work, "rebase", "--abort"], capture_output=True)
    die(
        f"the rebase onto {base} failed and was aborted -- nothing was pushed. "
        f"git's report is above; finish it by hand in {work}."
    )


def force_push(work, remote, branch, lease):
    """Force-push the rebased branch, refusing to overwrite an unseen commit.

    The lease is the commit the branch was fetched at rather than `--force-with-
    lease`'s default, which reads a remote-tracking ref any concurrent run's
    fetch may have moved since -- and a lease on a ref someone else refreshed
    protects nothing.
    """
    say(f"  force-pushing {branch} to {remote}")
    lease_flag = f"--force-with-lease={branch}:{lease}"
    if streamed("git", "-C", work, "push", lease_flag, remote, branch) != 0:
        die(f"the push to {remote} failed; {remote}/{branch} still has the old commits")


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
    default_branch = get_config(config, "project.defaultBranch", "master")

    repo, number, pr = read_pr(
        args.pr, config, args.repo, allow_input=not args.no_input
    )
    branch = pr["headRefName"]
    say(f"{repo}#{number} [{pr['state']}] {pr['title']}")
    say(f"  branch {branch}")

    # A merged or closed pull request is not waiting on its base branch, and
    # rewriting the branch behind one only loses the commits it was merged as.
    if pr["state"] != "OPEN":
        die(f"{repo}#{number} is {pr['state'].lower()}, so there is nothing to rebase")

    # The base comes from the config, the way every other rebase in this bot
    # picks one, so a pull request aimed elsewhere -- a release branch -- would
    # be rewritten onto a history it was never written against.
    if pr["baseRefName"] != default_branch:
        die(
            f"{repo}#{number} is against {pr['baseRefName']}, not the project's "
            f"{default_branch}, so this would rebase it onto the wrong branch"
        )

    owner = pr["headRepositoryOwner"]["login"]
    push_remote = remote_for_owner(main, owner)
    if not push_remote:
        die(
            f"no remote here points at {owner}, so the rebased branch could not "
            f"be pushed anywhere. Add one with `git -C {main} remote add`."
        )
    upstream = base_remote(main, repo)
    base = f"{upstream}/{default_branch}"

    work = worktree_for(bot, main, repo, number, pr)

    for remote in dict.fromkeys([upstream, push_remote]):
        say(f"  fetching {remote}")
        locked(bot, main, "fetch", remote)

    require_clean(work)
    remote_head = run("git", "-C", work, "rev-parse", f"{push_remote}/{branch}")
    align_with_remote(work, branch, remote_head)

    if is_ancestor(work, base, "HEAD"):
        say(f"  already on top of {base}; nothing to push")
        return

    # Said before the push rather than after it, because the approval is gone by
    # then: GitHub dismisses one when the commits it was given for are replaced.
    if pr["reviewDecision"] == "APPROVED":
        say("  Warning: this pull request is approved, and a force-push may")
        say("  dismiss the approval. Check it afterwards.")

    rebase(work, base)
    force_push(work, push_remote, branch, remote_head)
    say(f"  {repo}#{number} is now {run('git', '-C', work, 'rev-parse', 'HEAD')[:9]}")


if __name__ == "__main__":
    main_entry()
