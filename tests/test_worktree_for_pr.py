"""`make worktree`: resolving a pull request to the worktree holding its branch.

gh and direnv are stubbed on PATH, so nothing here reaches the network or the
machine's real direnv state. git is real: what the script has to get right is
git's own view of which worktree holds a branch.
"""

import json
import os
import subprocess

import pytest

ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPT = os.path.join(ROOT_DIR, "scripts", "worktree-for-pr.py")
BRANCH = "still-the-screen-and-the-colour"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def pr_json(head_oid, body="", state="OPEN", branch=BRANCH):
    return json.dumps(
        {
            "headRefName": branch,
            "headRefOid": head_oid,
            "headRepositoryOwner": {"login": "netzenbot"},
            "state": state,
            "title": "answer NO_COLOR",
            "body": body,
        }
    )


def graphql_json(issues=()):
    """What `gh api graphql` answers for the pull request's closing issues."""
    nodes = [{"number": n} for n in issues]
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {"closingIssuesReferences": {"nodes": nodes}}
                }
            }
        }
    )


class World:
    """A main checkout with the pull request's branch on a fork-like origin."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.main = tmp_path / "bravebot"
        # The remote URL has to carry the head repository's owner: that is how
        # the script picks which remote to fetch a pull request branch from.
        origin = tmp_path / "netzenbot" / "bravebot.git"
        origin.parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

        subprocess.run(["git", "init", "-q", "-b", "main", str(self.main)], check=True)
        git(self.main, "config", "user.name", "testbot")
        git(self.main, "config", "user.email", "testbot@example.com")
        git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "README").write_text("base\n")
        git(self.main, "add", "README")
        git(self.main, "commit", "-q", "-m", "base")
        git(self.main, "remote", "add", "origin", str(origin))

        git(self.main, "checkout", "-q", "-b", BRANCH)
        (self.main / "feature").write_text("work\n")
        git(self.main, "add", "feature")
        git(self.main, "commit", "-q", "-m", "the feature")
        self.head = git(self.main, "rev-parse", "HEAD")
        git(self.main, "push", "-q", "origin", BRANCH)
        # The branch must be checked out nowhere for the interesting cases.
        git(self.main, "checkout", "-q", "main")
        git(self.main, "branch", "-q", "-D", BRANCH)

        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.direnv_log = tmp_path / "direnv.log"
        # Two different calls: `gh pr view` and `gh api graphql` for the issue
        # link, which `gh pr view --json` cannot read before gh 2.46.
        self._stub(
            "gh",
            'if [ "$1" = api ]; then'
            ' [ -n "$GH_STUB_GRAPHQL" ] || exit 1;'
            ' printf "%s" "$GH_STUB_GRAPHQL";'
            ' else printf "%s" "$GH_STUB_JSON"; fi\n',
        )
        self._stub("direnv", f'echo "$@ in $PWD" >> {self.direnv_log}\n')

    def _stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def run(self, *args, gh_json=None, issues=(), graphql=None, expect=0):
        env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            GH_STUB_JSON=gh_json if gh_json is not None else pr_json(self.head),
            # Empty makes the stub fail, standing in for a gh too old for the
            # query or an API that did not answer.
            GH_STUB_GRAPHQL=graphql_json(issues) if graphql is None else graphql,
        )
        result = subprocess.run(
            [
                "python3",
                SCRIPT,
                "--main",
                str(self.main),
                "--repo",
                "brave/bravebot",
                "--no-input",
                *args,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == expect, result.stderr
        return result

    def sibling(self, suffix):
        return self.tmp / f"bravebot-{suffix}"


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ── Finding an existing worktree ─────────────────────────────────────────────


def test_reuses_the_worktree_that_holds_the_branch(world):
    """A worktree is matched by its branch, whatever its directory is called."""
    existing = world.sibling("105")
    git(world.main, "worktree", "add", "-q", str(existing), BRANCH)

    result = world.run("351")

    assert result.stdout.strip() == str(existing)
    assert "no worktree has it" not in result.stderr


def test_prunes_a_worktree_whose_directory_is_gone(world):
    """A registered-but-deleted worktree is replaced, not cd'd into."""
    stale = world.sibling("105")
    git(world.main, "worktree", "add", "-q", str(stale), BRANCH)
    subprocess.run(["rm", "-rf", str(stale)], check=True)

    result = world.run(
        "351", gh_json=pr_json(world.head, body="Part of brave/bravebot#105")
    )

    assert "pruning" in result.stderr
    assert os.path.isdir(result.stdout.strip())


def test_warns_when_the_branch_sits_in_the_main_checkout(world):
    """The profile keeps the main checkout clear, so landing there is worth saying."""
    git(world.main, "checkout", "-q", "-b", BRANCH, f"origin/{BRANCH}")

    result = world.run("351")

    assert result.stdout.strip() == str(world.main)
    assert "main checkout" in result.stderr


def test_warns_when_the_worktree_is_not_at_the_pull_requests_head(world):
    """A reused worktree may have been rebased or force-pushed past."""
    existing = world.sibling("105")
    git(world.main, "worktree", "add", "-q", str(existing), BRANCH)
    behind = git(world.main, "rev-parse", "HEAD")  # the base commit, on main

    result = world.run("351", gh_json=pr_json(behind))

    assert result.stdout.strip() == str(existing)
    assert "not what the pull request shows" in result.stderr


# ── Creating one ─────────────────────────────────────────────────────────────


def test_creates_a_worktree_named_after_the_issue(world):
    """`<main>-<issue>` is the directory the story's own worktree would use."""
    body = "Part of brave/bravebot#105\n\n## The problem\n"

    result = world.run("351", gh_json=pr_json(world.head, body=body))

    assert result.stdout.strip() == str(world.sibling("105"))
    assert git(world.sibling("105"), "rev-parse", "HEAD") == world.head
    assert git(world.sibling("105"), "rev-parse", "--abbrev-ref", "HEAD") == BRANCH


def test_closing_reference_beats_the_body(world):
    """GitHub's own link is authoritative when the pull request closes an issue."""
    body = "Part of brave/bravebot#999\n"

    result = world.run("351", gh_json=pr_json(world.head, body=body), issues=[105])

    assert result.stdout.strip() == str(world.sibling("105"))


def test_an_unanswered_issue_query_still_reads_the_body(world):
    """The pull request has to resolve on a gh too old for the closing-issue query."""
    body = "Part of brave/bravebot#105\n"

    result = world.run("351", gh_json=pr_json(world.head, body=body), graphql="")

    assert result.stdout.strip() == str(world.sibling("105"))


def test_falls_back_to_the_pull_request_number(world):
    """An issue with a second pull request cannot reuse the issue's directory."""
    world.sibling("105").mkdir()

    result = world.run(
        "351", gh_json=pr_json(world.head, body="Part of brave/bravebot#105")
    )

    assert result.stdout.strip() == str(world.sibling("pr351"))


def test_names_a_pull_request_with_no_issue_after_itself(world):
    result = world.run("351", gh_json=pr_json(world.head, body="## The problem\n"))

    assert result.stdout.strip() == str(world.sibling("pr351"))


def test_the_new_branch_tracks_the_fork(world):
    """Committing in the worktree has to have somewhere to push."""
    world.run("351")

    created = world.sibling("pr351")
    upstream = git(created, "rev-parse", "--abbrev-ref", f"{BRANCH}@{{upstream}}")
    assert upstream == f"origin/{BRANCH}"


# ── The environment the worktree starts with ─────────────────────────────────


def test_copies_the_envrc_and_allows_it(world):
    """Untracked files are not shared, so a new worktree has no environment."""
    (world.main / ".envrc").write_text("export BRAVEBOT_ENV=prod\n")

    result = world.run("351")

    created = world.sibling("pr351")
    assert (created / ".envrc").read_text() == "export BRAVEBOT_ENV=prod\n"
    assert "copied .envrc" in result.stderr
    assert f"allow in {created}" in world.direnv_log.read_text()


def test_leaves_an_existing_envrc_alone(world):
    """A worktree may have been pointed at another backend deliberately."""
    (world.main / ".envrc").write_text("export BRAVEBOT_ENV=prod\n")
    existing = world.sibling("105")
    git(world.main, "worktree", "add", "-q", str(existing), BRANCH)
    (existing / ".envrc").write_text("export BRAVEBOT_ENV=dev\n")

    world.run("351")

    assert (existing / ".envrc").read_text() == "export BRAVEBOT_ENV=dev\n"


def test_no_envrc_in_the_main_checkout_copies_nothing(world):
    world.run("351")

    assert not (world.sibling("pr351") / ".envrc").exists()
    assert not world.direnv_log.exists()


# ── Reading the pull request off the command line ────────────────────────────


def test_accepts_a_url_a_hash_and_a_bare_number(world, pr_worktree):
    parse = pr_worktree.parse_pr
    assert parse("https://github.com/brave/bravebot/pull/351", None) == (
        "brave/bravebot",
        "351",
    )
    assert parse("#351", "brave/bravebot") == ("brave/bravebot", "351")
    assert parse("351", "brave/bravebot") == ("brave/bravebot", "351")


def test_rejects_something_that_is_neither(pr_worktree):
    with pytest.raises(SystemExit):
        pr_worktree.parse_pr("not-a-pull-request", "brave/bravebot")


def test_asking_is_refused_when_there_is_no_one_to_ask(world):
    result = world.run(expect=1)

    assert "no pull request given" in result.stderr
