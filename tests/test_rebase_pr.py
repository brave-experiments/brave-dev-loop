"""`make rebase`: rebasing a pull request in its worktree and force-pushing it.

gh is stubbed on PATH, so nothing here reaches the network. Everything else is
real: two bare repositories stand in for the pull request's repository and the
bot's fork, and what the script has to get right is which one it rebases onto,
which one it pushes to, and when it refuses to push at all.
"""

import json
import os
import subprocess

import pytest

ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPT = os.path.join(ROOT_DIR, "scripts", "rebase-pr.py")
CONFIG = os.path.join(os.path.dirname(__file__), "config.test.json")
BRANCH = "lease-on-the-commit-we-fetched"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def graphql_json():
    """What `gh api graphql` answers for the pull request's closing issues.

    Empty: a pull request with no linked issue is named after itself, which
    keeps the worktree directory predictable for the assertions below.
    """
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {"closingIssuesReferences": {"nodes": []}}
                }
            }
        }
    )


class World:
    """A fork-based project: `upstream` holds the base branch, `origin` the branch.

    `upstream` has moved on since the pull request was pushed, so there is
    something to rebase onto. With `conflicting`, it moved on in the same file
    the pull request touches.
    """

    def __init__(self, tmp_path, base_branch="main", conflicting=False):
        self.tmp = tmp_path
        self.base_branch = base_branch
        self.main = tmp_path / "bravebot"
        # The remote URLs have to carry the owner GitHub names, since that is
        # how the script picks the remote to push a head branch back to.
        self.upstream = self._bare(tmp_path / "brave" / "bravebot.git")
        self.origin = self._bare(tmp_path / "netzenbot" / "bravebot.git")

        subprocess.run(
            ["git", "init", "-q", "-b", base_branch, str(self.main)], check=True
        )
        git(self.main, "config", "user.name", "testbot")
        git(self.main, "config", "user.email", "testbot@example.com")
        git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "README").write_text("base\n")
        git(self.main, "add", "README")
        git(self.main, "commit", "-q", "-m", "base")
        self.base = git(self.main, "rev-parse", "HEAD")
        git(self.main, "remote", "add", "upstream", str(self.upstream))
        git(self.main, "remote", "add", "origin", str(self.origin))
        git(self.main, "push", "-q", "upstream", base_branch)
        git(self.main, "push", "-q", "origin", base_branch)

        git(self.main, "checkout", "-q", "-b", BRANCH)
        (self.main / "feature").write_text("ours\n")
        git(self.main, "add", "feature")
        git(self.main, "commit", "-q", "-m", "the feature")
        self.head = git(self.main, "rev-parse", "HEAD")
        git(self.main, "push", "-q", "origin", BRANCH)

        git(self.main, "checkout", "-q", base_branch)
        (self.main / ("feature" if conflicting else "CHANGELOG")).write_text("theirs\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "someone else's commit")
        self.moved = git(self.main, "rev-parse", "HEAD")
        git(self.main, "push", "-q", "upstream", base_branch)
        # Checked out nowhere, which is where a real main checkout leaves it.
        git(self.main, "branch", "-q", "-D", BRANCH)
        self.main_head = git(self.main, "rev-parse", "HEAD")

        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        # Two different calls: `gh pr view` and `gh api graphql` for the issue
        # link, which `gh pr view --json` cannot read before gh 2.46.
        stub = self.bin / "gh"
        stub.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = api ]; then printf "%s" "$GH_STUB_GRAPHQL";'
            ' else printf "%s" "$GH_STUB_JSON"; fi\n'
        )
        stub.chmod(0o755)

    def _bare(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
        return path

    def _config(self):
        """A config whose defaultBranch is this world's base branch."""
        with open(CONFIG) as f:
            config = json.load(f)
        config["project"]["defaultBranch"] = self.base_branch
        path = self.tmp / "config.json"
        path.write_text(json.dumps(config))
        return path

    def run(self, *args, state="OPEN", review=None, base=None, expect=0):
        pr = {
            "headRefName": BRANCH,
            "headRefOid": self.head,
            "headRepositoryOwner": {"login": "netzenbot"},
            "baseRefName": base or self.base_branch,
            "state": state,
            "title": "answer NO_COLOR",
            "body": "",
            "reviewDecision": review,
        }
        env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            BOT_CONFIG_FILE=str(self._config()),
            GH_STUB_JSON=json.dumps(pr),
            GH_STUB_GRAPHQL=graphql_json(),
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
                "351",
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

    def add_worktree(self, suffix):
        """The worktree a story would already have for this branch."""
        dest = self.sibling(suffix)
        git(
            self.main,
            "worktree",
            "add",
            "-q",
            "--track",
            "-b",
            BRANCH,
            str(dest),
            f"origin/{BRANCH}",
        )
        return dest

    def pushed(self):
        return git(self.origin, "rev-parse", BRANCH)

    def subjects(self):
        return git(self.origin, "log", "--format=%s", BRANCH).splitlines()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ── The rebase ───────────────────────────────────────────────────────────────


def test_rebases_onto_upstream_and_force_pushes(world):
    """The base is the branch a reviewer merges into, not where it was pushed."""
    world.run()

    assert world.subjects() == ["the feature", "someone else's commit", "base"]
    assert git(world.origin, "rev-parse", f"{BRANCH}~1") == world.moved


def test_the_push_goes_to_the_fork_the_branch_came_from(world):
    """`upstream` is what it rebases onto; nothing is ever pushed there."""
    world.run()

    upstream_branches = git(
        world.upstream, "for-each-ref", "--format=%(refname:short)", "refs/heads"
    )
    assert BRANCH not in upstream_branches.splitlines()


def test_rebases_onto_the_configured_default_branch(tmp_path):
    """Projects differ: this one calls it master, and nothing may assume main."""
    world = World(tmp_path, base_branch="master")

    world.run()

    assert git(world.origin, "rev-parse", f"{BRANCH}~1") == world.moved


def test_warns_that_the_push_may_dismiss_an_approval(world):
    """The approval is gone by the time the push reports, so say it beforehand."""
    result = world.run(review="APPROVED")

    assert "dismiss the approval" in result.stderr
    assert world.pushed() != world.head


def test_a_second_run_finds_nothing_to_push(world):
    world.run()
    rebased = world.pushed()

    result = world.run()

    assert "already on top" in result.stderr
    assert world.pushed() == rebased


# ── Which tree it works in ───────────────────────────────────────────────────


def test_the_main_checkout_never_moves(world):
    """It holds the default branch every worktree borrows from."""
    world.run()

    assert git(world.main, "rev-parse", "HEAD") == world.main_head
    assert git(world.main, "rev-parse", "--abbrev-ref", "HEAD") == world.base_branch
    assert git(world.sibling("pr351"), "rev-parse", "HEAD") == world.pushed()


def test_reuses_the_worktree_that_holds_the_branch(world):
    """A story's worktree is matched by branch, whatever its directory is called."""
    existing = world.add_worktree("105")

    world.run()

    assert not world.sibling("pr351").exists()
    assert git(existing, "rev-parse", "HEAD") == world.pushed()


def test_fast_forwards_a_worktree_left_behind(world):
    """Behind the branch is a fast-forward: every commit here is in it already."""
    existing = world.add_worktree("105")
    git(existing, "reset", "--hard", world.base)

    result = world.run()

    assert "fast-forwarding" in result.stderr
    assert git(world.origin, "rev-parse", f"{BRANCH}~1") == world.moved
    assert git(existing, "rev-parse", "HEAD") == world.pushed()


# ── When it refuses ──────────────────────────────────────────────────────────


def test_refuses_a_tree_with_uncommitted_changes(world):
    """A session may be mid-edit in this worktree right now."""
    existing = world.add_worktree("105")
    (existing / "feature").write_text("half a fix\n")

    result = world.run(expect=1)

    assert "uncommitted changes" in result.stderr
    assert world.pushed() == world.head
    assert (existing / "feature").read_text() == "half a fix\n"


def test_untracked_files_do_not_block_the_rebase(world):
    """A worktree is full of them: a build directory, a screenshot, a log."""
    existing = world.add_worktree("105")
    (existing / "screenshot.png").write_text("not in git\n")

    world.run()

    assert git(world.origin, "rev-parse", f"{BRANCH}~1") == world.moved
    assert (existing / "screenshot.png").exists()


def test_refuses_commits_the_pull_request_has_never_had(world):
    """Force-pushing those would add unreviewed work under the name of a rebase."""
    existing = world.add_worktree("105")
    (existing / "extra").write_text("never pushed\n")
    git(existing, "add", "extra")
    git(existing, "commit", "-q", "-m", "a fix nobody has seen")
    local = git(existing, "rev-parse", "HEAD")

    result = world.run(expect=1)

    assert "commits the pull request does not" in result.stderr
    assert world.pushed() == world.head
    assert git(existing, "rev-parse", "HEAD") == local


def test_a_conflict_aborts_the_rebase_and_pushes_nothing(tmp_path):
    """An abandoned rebase would strand the next session in this worktree."""
    world = World(tmp_path, conflicting=True)

    result = world.run(expect=1)

    assert "failed and was aborted" in result.stderr
    assert world.pushed() == world.head
    work = world.sibling("pr351")
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == BRANCH
    assert git(work, "rev-parse", "HEAD") == world.head
    assert git(work, "status", "--porcelain") == ""


def test_refuses_a_pull_request_against_another_branch(world):
    """The base is the project's default branch, so a release branch is not it."""
    result = world.run(base="1.79.x", expect=1)

    assert "is against 1.79.x" in result.stderr
    assert world.pushed() == world.head


def test_refuses_a_merged_pull_request(world):
    """Rewriting that branch only loses the commits it was merged as."""
    result = world.run(state="MERGED", expect=1)

    assert "merged" in result.stderr
    assert world.pushed() == world.head
    assert not world.sibling("pr351").exists()


# ── Picking the remote to rebase onto ────────────────────────────────────────


def test_the_base_remote_is_upstream_where_the_project_has_a_fork(world, rebase_pr):
    assert rebase_pr.base_remote(str(world.main), "brave/bravebot") == "upstream"


def test_the_base_remote_can_be_named_something_else(world, rebase_pr):
    git(world.main, "remote", "rename", "upstream", "public")

    assert rebase_pr.base_remote(str(world.main), "brave/bravebot") == "public"


def test_without_a_fork_the_base_remote_is_origin(world, rebase_pr):
    git(world.main, "remote", "remove", "upstream")

    assert rebase_pr.base_remote(str(world.main), "brave/bravebot") == "origin"
