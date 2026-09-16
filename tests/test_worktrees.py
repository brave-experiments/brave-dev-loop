"""Tests for the worktree half of a run: provisioning one, and collecting it.

Both halves are git behaviour, not logic that can be faked — whether the
post-checkout hook fires on `git worktree add` at all, and whether a worktree
holding unpushed work is recognised as such. So these build real repositories
with a real remote and run real git against them. Each is a few files and one
commit, so the whole file takes about a second.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SCRIPTS = os.path.join(ROOT, "scripts")
LIB = os.path.join(SCRIPTS, "lib")
CLEAN_WORKTREES = os.path.join(SCRIPTS, "clean-worktrees.py")
POST_CHECKOUT = os.path.join(ROOT, "hooks", "post-checkout")

sys.path.insert(0, SCRIPTS)
from lib import claims as claims_lib  # noqa: E402

BOT_USER = "netzenbot"


def git(*args, cwd):
    return subprocess.run(
        ["git"] + list(args), cwd=cwd, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path):
    """A checkout with an `origin` it has pushed `main` to, plus a bot identity.

    `origin` is what makes the "is this commit on a remote" check meaningful:
    without a remote nothing is ever collectable, which is safe but tests
    nothing.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True
    )

    main = tmp_path / "repo"
    main.mkdir()
    git("init", "-q", "-b", "main", ".", cwd=main)
    git("config", "user.name", BOT_USER, cwd=main)
    git("config", "user.email", "netzenbot@agentmail.to", cwd=main)
    git("config", "commit.gpgsign", "false", cwd=main)
    git("remote", "add", "origin", str(origin), cwd=main)
    (main / ".gitignore").write_text(".envrc\n.env\n")
    git("add", ".gitignore", cwd=main)
    git("commit", "-qm", "first", cwd=main)
    git("push", "-q", "origin", "main", cwd=main)
    git("fetch", "-q", "origin", cwd=main)
    return main


@pytest.fixture
def bot_dir(tmp_path):
    """A bot directory with an empty PRD, for the claim lookups."""
    d = tmp_path / "bot"
    (d / "data").mkdir(parents=True)
    (d / "data" / "prd.json").write_text(json.dumps({"stories": []}) + "\n")
    return d


@pytest.fixture
def live_run(bot_dir):
    """A background process holding a run slot, the way a live run.sh does.

    A claim counts only while the run that took it still holds its slot lock, so
    there is no way to test that a claim protects a worktree without one really
    held. Yields the (slot, pid) to claim with.
    """
    proc = subprocess.Popen(
        [
            "bash",
            "-c",
            f"source {LIB}/run-slots.sh\n"
            f"bot_acquire_run_slot {bot_dir} 2 || exit 1\n"
            'echo "$BOT_RUN_SLOT"\n'
            "sleep 120\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    slot = int(proc.stdout.readline().strip())
    yield slot, proc.pid
    proc.kill()
    proc.wait(timeout=10)


def add_worktree(repo, name, branch=None):
    path = repo.parent / name
    args = ["worktree", "add", "-q", str(path)]
    if branch:
        args += ["-b", branch]
    else:
        args += ["--detach"]
    git(*args, cwd=repo)
    return path


def install_post_checkout(repo, user=BOT_USER):
    """Install the hook the way scripts/setup.sh does, substitution included."""
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    dest = hooks / "post-checkout"
    body = open(POST_CHECKOUT).read().replace("__BOT_USERNAME__", user)
    dest.write_text(body)
    dest.chmod(0o755)
    return dest


def clean(repo, bot_dir, *extra):
    """Run clean-worktrees.py against this repo, returning its JSON report."""
    result = subprocess.run(
        [
            sys.executable,
            CLEAN_WORKTREES,
            "--repo",
            str(repo),
            "--prd",
            str(bot_dir / "data" / "prd.json"),
            *extra,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ── Provisioning: hooks/post-checkout ────────────────────────────────────────


def test_new_worktree_gets_the_main_checkout_env(repo):
    (repo / ".envrc").write_text("export SERVICES_KEY=real\n")
    install_post_checkout(repo)

    worktree = add_worktree(repo, "repo-133", branch="fix-133")

    assert (worktree / ".envrc").read_text() == "export SERVICES_KEY=real\n"


def test_env_copies_are_not_something_a_commit_can_pick_up(repo):
    """The point of copying rather than committing: the copy stays ignored."""
    (repo / ".envrc").write_text("export SERVICES_KEY=real\n")
    install_post_checkout(repo)

    worktree = add_worktree(repo, "repo-133", branch="fix-133")

    status = git("status", "--porcelain", cwd=worktree).stdout
    assert status.strip() == ""


def test_a_worktree_with_no_env_in_the_main_checkout_is_left_alone(repo):
    install_post_checkout(repo)

    worktree = add_worktree(repo, "repo-133", branch="fix-133")

    assert not (worktree / ".envrc").exists()
    assert not (worktree / ".env").exists()


def test_the_main_checkout_is_never_its_own_target(repo):
    """A `git checkout` in the main clone must not be treated as a new worktree."""
    (repo / ".envrc").write_text("export SERVICES_KEY=real\n")
    install_post_checkout(repo)

    git("checkout", "-q", "-b", "other", cwd=repo)

    assert (repo / ".envrc").read_text() == "export SERVICES_KEY=real\n"


def test_a_clone_belonging_to_a_person_is_untouched(repo):
    """Gated on the bot identity, the same way the other hooks are."""
    (repo / ".envrc").write_text("export SERVICES_KEY=real\n")
    install_post_checkout(repo)
    git("config", "user.name", "Some Person", cwd=repo)

    worktree = add_worktree(repo, "repo-133", branch="fix-133")

    assert not (worktree / ".envrc").exists()


# ── Collection: scripts/clean-worktrees.py ───────────────────────────────────


def test_a_pushed_worktree_is_collected(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)

    report = clean(repo, bot_dir)

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()
    assert str(worktree) not in git("worktree", "list", cwd=repo).stdout


def test_the_main_checkout_is_never_collected(repo, bot_dir):
    report = clean(repo, bot_dir)

    assert report["removed"] == []
    assert (repo / ".git").exists()


def test_uncommitted_changes_keep_a_worktree(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)
    (worktree / "scratch.txt").write_text("half a fix\n")

    report = clean(repo, bot_dir)

    assert report["removed"] == []
    assert worktree.exists()
    assert "uncommitted changes" in report["kept"][0]["reason"]


def test_commits_no_remote_has_keep_a_worktree(repo, bot_dir):
    """A story mid-development: committed in the worktree, not yet pushed."""
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    (worktree / "fix.txt").write_text("the fix\n")
    git("add", "fix.txt", cwd=worktree)
    git("commit", "-qm", "the fix", cwd=worktree)

    report = clean(repo, bot_dir)

    assert report["removed"] == []
    assert worktree.exists()
    assert "on no remote branch" in report["kept"][0]["reason"]


def test_a_worktree_a_live_run_claims_is_kept(repo, bot_dir, live_run):
    """The claim is the only thing protecting a worktree a run just created.

    It is clean and sitting on a commit origin has, which is exactly what a
    freshly added worktree looks like — indistinguishable from finished work
    except that a run holds the story.
    """
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)
    prd = bot_dir / "data" / "prd.json"
    prd.write_text(
        json.dumps(
            {
                "stories": [
                    {
                        "id": "US-001",
                        "description": "Resolve issue #133",
                        "status": "pending",
                    }
                ]
            }
        )
    )
    slot, pid = live_run
    assert claims_lib.claim(str(bot_dir), "US-001", slot=slot, pid=pid)

    report = clean(repo, bot_dir)

    assert report["removed"] == []
    assert worktree.exists()
    assert report["kept"][0]["reason"] == "a live run is working in it"


def test_a_claimed_story_is_matched_by_branch_too(repo, bot_dir, live_run):
    """Worktrees off the `<repo>-<issue>` convention are still protected."""
    worktree = add_worktree(repo, "repo-rustls", branch="update-rustls")
    git("push", "-q", "origin", "update-rustls", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)
    prd = bot_dir / "data" / "prd.json"
    prd.write_text(
        json.dumps(
            {
                "stories": [
                    {
                        "id": "US-001",
                        "description": "no issue reference",
                        "branchName": "update-rustls",
                        "status": "committed",
                    }
                ]
            }
        )
    )
    slot, pid = live_run
    assert claims_lib.claim(str(bot_dir), "US-001", slot=slot, pid=pid)

    report = clean(repo, bot_dir)

    assert report["removed"] == []
    assert worktree.exists()


def test_max_age_hours_keeps_a_worktree_added_just_now(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)

    report = clean(repo, bot_dir, "--max-age-hours", "24")

    assert report["removed"] == []
    assert worktree.exists()
    assert report["kept"][0]["reason"] == "it was added too recently"


def test_max_age_hours_collects_one_that_is_old_enough(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)
    marker = worktree / ".git"
    two_days_ago = os.path.getmtime(marker) - 2 * 24 * 3600
    os.utime(marker, (two_days_ago, two_days_ago))

    report = clean(repo, bot_dir, "--max-age-hours", "24")

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()


def test_dry_run_reports_without_removing(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)

    report = clean(repo, bot_dir, "--dry-run")

    assert report["removed"] == [str(worktree)]
    assert report["dryRun"] is True
    assert worktree.exists()
    assert (worktree / ".git").exists()


def test_a_directory_left_behind_by_a_killed_run_is_swept(repo, bot_dir):
    """Removal renames first and deletes after: the rename can outlive the run."""
    leftover = repo.parent / "repo-133.deleting"
    leftover.mkdir()
    (leftover / "target").mkdir()

    report = clean(repo, bot_dir)

    assert report["swept"] == [str(leftover)]
    assert not leftover.exists()


def test_a_worktree_deleted_by_hand_stops_being_registered(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    subprocess.run(["rm", "-rf", str(worktree)], check=True)

    clean(repo, bot_dir)

    assert "repo-133" not in git("worktree", "list", cwd=repo).stdout


def test_the_branch_survives_so_the_next_iteration_can_reuse_it(repo, bot_dir):
    """A story's worktree is disposable; the branch behind its PR is not."""
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)

    clean(repo, bot_dir)

    branches = git("branch", "--list", "fix-133", cwd=repo).stdout
    assert "fix-133" in branches
