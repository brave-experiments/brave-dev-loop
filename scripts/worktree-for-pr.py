#!/usr/bin/env python3
"""Resolve the git worktree for a pull request, creating it when there is none.

Prints the worktree's absolute path on stdout and nothing else, so a caller can
cd into it:

    cd "$(scripts/worktree-for-pr.py https://github.com/brave/bravebot/pull/351)"

`make worktree PR=<url>` is the intended entry point -- a child process cannot
change its parent's directory, so the Makefile does the cd and opens a shell.

Progress, warnings and the prompt for a missing pull request all go to the
terminal rather than stdout, so capturing stdout still leaves the user informed.

No model is involved: gh and git decide everything here.
"""

import argparse
import json
import os
import re
import shutil

# Every call below is git, gh or direnv with an argument list and no shell, and
# the only value reaching one that this script did not choose is a branch name
# gh read from the pull request.
import subprocess  # nosemgrep
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.load_config import bot_dir, get_config, load_config, resolve_target_repo

# "Closes brave/bravebot#105" or "Part of #105", as the first line of a body.
# docs/pr-descriptions.md requires the closing line there, and a partial fix
# opens with "Part of" instead -- either one names the story's issue.
ISSUE_LINE = re.compile(
    r"^\s*(?:closes|fixes|resolves|part of)\s+\S*?#(\d+)", re.IGNORECASE
)
PR_URL = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")

PR_FIELDS = (
    "headRefName,headRefOid,headRepositoryOwner,state,title,body,"
    "closingIssuesReferences"
)


def say(message):
    print(message, file=sys.stderr)


def die(message):
    say(f"Error: {message}")
    sys.exit(1)


def run(*args, **kwargs):
    """Capture a command's stdout, raising CalledProcessError on failure."""
    return subprocess.run(
        args, check=True, capture_output=True, text=True, **kwargs
    ).stdout.strip()


def parse_pr(ref, default_repo):
    """Split a URL, '#351' or '351' into (repo, number)."""
    ref = ref.strip()
    match = PR_URL.search(ref)
    if match:
        return match.group(1), match.group(2)
    digits = ref.lstrip("#")
    if digits.isdigit():
        if not default_repo:
            die(f"'{ref}' has no repository and project.prRepository is not set")
        return default_repo, digits
    die(f"'{ref}' is neither a pull request URL nor a number")


def ask_for_pr():
    """Prompt for the pull request, asking on stderr rather than stdout.

    stdout carries the worktree path for the caller to cd into, so a question
    printed there would be read as the answer.
    """
    if not sys.stdin.isatty():
        die("no pull request given: make worktree PR=<url|number>")
    print("Pull request (URL or number): ", end="", file=sys.stderr, flush=True)
    answer = sys.stdin.readline().strip()
    if not answer:
        die("no pull request given")
    return answer


def pr_details(repo, number):
    try:
        raw = run("gh", "pr", "view", number, "--repo", repo, "--json", PR_FIELDS)
    except subprocess.CalledProcessError as exc:
        die(f"gh could not read {repo}#{number}: {exc.stderr.strip()}")
    return json.loads(raw)


def worktree_for_branch(main, branch):
    """The worktree holding `branch`, or None. Includes the main checkout."""
    listing = run("git", "-C", main, "worktree", "list", "--porcelain")
    path = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def locked(bot, main, *args):
    """Run a git command that touches the shared .git, under the repo lock.

    Worktrees share one .git and several runs may be in it at once; a fetch or
    a `worktree add` that collides with another one fails on git's ref locks.
    """
    lock = os.path.join(bot, "scripts", "git-repo-lock.sh")
    return run(lock, main, "--", "git", "-C", main, *args)


def remotes(main):
    """Remote name -> URL."""
    found = {}
    for line in run("git", "-C", main, "remote", "-v").splitlines():
        name, _, rest = line.partition("\t")
        if name and name not in found:
            found[name] = rest.split()[0] if rest else ""
    return found


def remote_for_owner(main, owner):
    """The remote pointing at `owner`'s fork, preferring origin.

    Matching on the owner alone is deliberate: this repository was renamed, so
    the remotes say brave-experiments/bravebot where the config and gh say
    brave/bravebot, and matching owner/name would find nothing.
    """
    matches = [
        name
        for name, url in remotes(main).items()
        if f"{owner.lower()}/" in url.lower()
    ]
    if "origin" in matches:
        return "origin"
    return matches[0] if matches else None


def branch_exists(main, branch):
    return (
        subprocess.run(
            ["git", "-C", main, "rev-parse", "--verify", "--quiet", branch],
            capture_output=True,
        ).returncode
        == 0
    )


def issue_number(pr):
    """The issue this pull request belongs to, or None.

    A partial fix has no closing reference -- it opens with "Part of" so as not
    to close an issue it only half fixes -- so the body's first line is read
    when GitHub reports no linked issue.
    """
    for ref in pr.get("closingIssuesReferences") or []:
        if ref.get("number"):
            return str(ref["number"])
    lines = (pr.get("body") or "").splitlines()
    if lines:
        match = ISSUE_LINE.match(lines[0])
        if match:
            return match.group(1)
    return None


def target_dir(main, pr, number):
    """Where a new worktree goes: a free sibling directory of the main checkout.

    `<main>-<issue>` is the convention every story's worktree follows, so a pull
    request lands in the same directory its story would have used. That name is
    taken when the issue already has a worktree on some other branch -- a
    second pull request for the same issue -- and then the pull request's own
    number names it instead.
    """
    issue = issue_number(pr)
    candidates = [f"{main}-{issue}"] if issue else []
    candidates.append(f"{main}-pr{number}")
    for candidate in candidates:
        if not os.path.exists(candidate):
            return candidate
    die(f"{' and '.join(candidates)} both exist already; remove one first")


def create(bot, main, repo, number, branch, owner, dest):
    """Add a worktree for the pull request's branch at `dest`."""
    say(f"  no worktree has it; creating {dest}")

    if branch_exists(main, branch):
        # Already local -- and checked out nowhere, or the search found it.
        locked(bot, main, "worktree", "add", dest, branch)
        return

    remote = remote_for_owner(main, owner)
    if remote:
        locked(bot, main, "fetch", remote, branch)
        track = ["--track", "-b", branch, dest, f"{remote}/{branch}"]
        locked(bot, main, "worktree", "add", *track)
        return

    # A fork nothing here has a remote for -- a contributor's, not the bot's.
    # GitHub serves the head ref from the pull request's own repository, so one
    # fetch from upstream gets it; pushing back would need a remote for the
    # fork, which is the owner's to add.
    base = "upstream" if "upstream" in remotes(main) else "origin"
    say(f"  {owner} has no remote here; fetching {repo}#{number} head from {base}")
    locked(bot, main, "fetch", base, f"pull/{number}/head:{branch}")
    locked(bot, main, "worktree", "add", dest, branch)


def copy_envrc(main, dest):
    """Give the worktree the main checkout's .envrc.

    .envrc is gitignored, and untracked files are not shared between worktrees,
    so a fresh one has no environment at all and everything built there needs
    BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 instead. An existing one is left alone:
    it may have been pointed at another backend deliberately.
    """
    source = os.path.join(main, ".envrc")
    target = os.path.join(dest, ".envrc")
    if not os.path.exists(source) or os.path.exists(target):
        return
    shutil.copy2(source, target)
    say(f"  copied .envrc from {main}")


def allow_direnv(dest):
    """Whitelist the worktree's .envrc so the shell loads it on arrival."""
    if not shutil.which("direnv"):
        return
    if not os.path.exists(os.path.join(dest, ".envrc")):
        return
    result = subprocess.run(["direnv", "allow"], cwd=dest, capture_output=True)
    if result.returncode != 0:
        say("  warning: direnv allow failed; run it yourself in the worktree")


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
    main = args.main or resolve_target_repo(config)
    if not main or not os.path.exists(os.path.join(main, ".git")):
        die(f"'{main}' is not a git repository; set project.targetRepoPath")
    main = os.path.realpath(main)

    ref = args.pr or ("" if args.no_input else ask_for_pr())
    if not ref:
        die("no pull request given: make worktree PR=<url|number>")

    default_repo = args.repo or get_config(config, "project.prRepository")
    repo, number = parse_pr(ref, default_repo)
    pr = pr_details(repo, number)
    branch = pr["headRefName"]
    say(f"{repo}#{number} [{pr['state']}] {pr['title']}")
    say(f"  branch {branch}")

    dest = worktree_for_branch(main, branch)
    if dest and not os.path.isdir(dest):
        say(f"  {dest} is registered but gone; pruning")
        locked(bot, main, "worktree", "prune")
        dest = None

    if dest:
        say(f"  worktree {dest}")
        if dest == main:
            say(
                "  Warning: that is the main checkout, which this profile keeps "
                "clear -- nothing should be built or committed there."
            )
    else:
        dest = target_dir(main, pr, number)
        create(
            bot, main, repo, number, branch, pr["headRepositoryOwner"]["login"], dest
        )

    warn_if_behind(dest, pr["headRefOid"])
    copy_envrc(main, dest)
    allow_direnv(dest)
    print(dest)


if __name__ == "__main__":
    main_entry()
