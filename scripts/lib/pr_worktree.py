"""Resolve a pull request to the git worktree holding its branch.

Two commands need the same answer: `scripts/worktree-for-pr.py` prints the path
for a shell to cd into, and `scripts/rebase-pr.py` rebases the branch in it. The
resolution lives here so neither one owns it.

A worktree is found by *branch*, since a worktree's directory name follows its
story's issue and not the branch. When none has it, one is created from the pull
request's head in the directory that story's own worktree would use.

No model is involved: gh and git decide everything here.
"""

import json
import os
import re
import shutil

# Every call below is git, gh or direnv with an argument list and no shell, and
# the only value reaching one that this module did not choose is a branch name
# gh read from the pull request.
import subprocess  # nosemgrep
import sys

from .load_config import get_config, resolve_target_repo

# "Closes brave/bravebot#105" or "Part of #105", as the first line of a body.
# docs/pr-descriptions.md requires the closing line there, and a partial fix
# opens with "Part of" instead -- either one names the story's issue.
ISSUE_LINE = re.compile(
    r"^\s*(?:closes|fixes|resolves|part of)\s+\S*?#(\d+)", re.IGNORECASE
)
PR_URL = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")

PR_FIELDS = (
    "headRefName,headRefOid,headRepositoryOwner,baseRefName,"
    "state,title,body,reviewDecision"
)

# `gh pr view --json closingIssuesReferences` only exists from gh 2.46, and an
# unknown field fails the whole read, so the link comes from GraphQL instead --
# every gh version can reach it.
CLOSING_ISSUES_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 10) { nodes { number } }
    }
  }
}
"""


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

    worktree-for-pr.py's stdout carries the worktree path for the caller to cd
    into, so a question printed there would be read as the answer.
    """
    if not sys.stdin.isatty():
        die("no pull request given: PR=<url|number>")
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
    pr = json.loads(raw)
    pr["closingIssuesReferences"] = closing_issues(repo, number)
    return pr


def closing_issues(repo, number):
    """The issues this pull request closes, as `[{"number": n}, ...]`.

    An empty list stands for both "closes nothing" and "the query failed": the
    only caller falls back to the body's first line, which is enough to name a
    worktree, so a GraphQL failure is not worth stopping for.
    """
    owner, _, name = repo.partition("/")
    try:
        raw = run(
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={CLOSING_ISSUES_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"number={number}",
        )
        pr = json.loads(raw)["data"]["repository"]["pullRequest"]
        return pr["closingIssuesReferences"]["nodes"]
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, TypeError):
        return []


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


def is_ancestor(work, earlier, later):
    return (
        subprocess.run(
            ["git", "-C", work, "merge-base", "--is-ancestor", earlier, later],
            capture_output=True,
        ).returncode
        == 0
    )


def have_commit(main, oid):
    """Whether this repository holds the commit `oid` at all.

    A pull request's head comes from gh, so it names a commit GitHub has and this
    checkout may never have fetched -- and nothing can be said about a commit
    that is not here, let alone moved onto it.
    """
    return (
        subprocess.run(
            ["git", "-C", main, "cat-file", "-e", f"{oid}^{{commit}}"],
            capture_output=True,
        ).returncode
        == 0
    )


def uncommitted(work):
    """Changes to tracked files in `work` that nobody has committed.

    Untracked files are not counted -- a build directory and a screenshot are
    what a worktree is full of, and moving a tree only minds a file it would
    have to overwrite.
    """
    return run("git", "-C", work, "status", "--porcelain", "-uno")


def fetch_head(bot, main, repo, number, branch, owner):
    """Fetch the pull request's branch, from wherever this repository can see it.

    Forced, because the interesting case is a branch that was rewritten: an
    unforced fetch of one is refused, which is exactly when the ref here is
    furthest from the pull request. A fork nothing here has a remote for is read
    through the pull request itself, which GitHub serves from the base
    repository -- enough to have the commit, which is all this is for.
    """
    remote = remote_for_owner(main, owner)
    if remote:
        say(f"  fetching {branch} from {remote}")
        refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
        locked(bot, main, "fetch", remote, refspec)
        return

    base = "upstream" if "upstream" in remotes(main) else "origin"
    say(f"  {owner} has no remote here; fetching {repo}#{number} head from {base}")
    locked(bot, main, "fetch", base, f"+refs/pull/{number}/head")


def align_with_remote(work, branch, remote_head, on_refuse):
    """Put the worktree on the commit the pull request's branch was pushed at.

    A worktree is wherever its last session left it. Behind the branch -- every
    commit in it already contained in `remote_head` -- is a fast-forward and
    loses nothing. Ahead of it is different: the tree holds a commit the pull
    request has never had, which is what a local rebase or an unpushed fix looks
    like, and what that means belongs to the caller. `make rebase` stops, because
    force-pushing would add the commit to the pull request under the name of a
    rebase; `make worktree` says so and reads the tree where it stands. A dirty
    tree is the caller's to rule out first, with `uncommitted`.
    """
    local = run("git", "-C", work, "rev-parse", "HEAD")
    if local == remote_head:
        return
    if not is_ancestor(work, local, remote_head):
        on_refuse(local, remote_head)
        return
    say(f"  fast-forwarding {local[:9]} -> {remote_head[:9]}")
    run("git", "-C", work, "reset", "--hard", remote_head)


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


def main_checkout(config, override=None):
    """The target repo's main checkout, absolute and symlink-free."""
    main = override or resolve_target_repo(config)
    if not main or not os.path.exists(os.path.join(main, ".git")):
        die(f"'{main}' is not a git repository; set project.targetRepoPath")
    return os.path.realpath(main)


def read_pr(ref, config, repo_override=None, allow_input=True):
    """(repo, number, pr details) for a pull request URL, '#351' or bare '351'."""
    ref = ref or (ask_for_pr() if allow_input else "")
    if not ref:
        die("no pull request given: PR=<url|number>")
    default_repo = repo_override or get_config(config, "project.prRepository")
    repo, number = parse_pr(ref, default_repo)
    return repo, number, pr_details(repo, number)


def worktree_for(bot, main, repo, number, pr):
    """The worktree holding the pull request's branch, creating one if needed.

    Either way it comes back with the main checkout's .envrc in it, allowed.
    """
    branch = pr["headRefName"]
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

    copy_envrc(main, dest)
    allow_direnv(dest)
    return dest
