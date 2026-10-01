"""sync-target-repo.sh: what it does to a checkout a killed run left broken.

This script runs unattended from cron, first in an `&&` chain, so what it does
when it meets a broken checkout decides whether the jobs behind it run at all.
It has three jobs, and each is tested here: heal the mechanical breakages a
killed git leaves behind, never destroy work that is not committed, and never
exit non-zero.
"""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(__file__), os.pardir)


def git(*args, cwd):
    return subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
        ]
        + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def deployment(tmp_path):
    """A bot directory, an upstream with two commits, and a target cloned from it.

    `origin` and `upstream` are separate repos so the mirroring push at the end
    of the script is exercised rather than skipped.
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git("init", "-q", "-b", "master", ".", cwd=upstream)
    git("commit", "-q", "--allow-empty", "-m", "first", cwd=upstream)
    (upstream / "f.txt").write_text("upstream\n")
    git("add", "f.txt", cwd=upstream)
    git("commit", "-qm", "second", cwd=upstream)

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "master", str(origin)], check=True
    )

    target = tmp_path / "target"
    subprocess.run(
        ["git", "clone", "-q", "--origin", "upstream", str(upstream), str(target)],
        check=True,
    )
    git("remote", "add", "origin", str(origin), cwd=target)
    git("push", "-q", "origin", "master", cwd=target)

    bot = tmp_path / "bot"
    bot.mkdir()
    shutil.copytree(
        os.path.join(ROOT, "scripts"),
        bot / "scripts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (bot / "config.json").write_text(
        json.dumps(
            {
                "project": {
                    "name": "t",
                    "org": "o",
                    "prRepository": "o/p",
                    "issueRepository": "o/p",
                    "defaultBranch": "master",
                    "targetRepoPath": str(target),
                },
                "bot": {"username": "b"},
            }
        )
    )
    return bot, target


def sync(bot):
    return subprocess.run(
        [str(bot / "scripts" / "sync-target-repo.sh")],
        capture_output=True,
        text=True,
    )


def test_a_clean_checkout_syncs_to_upstream(deployment):
    bot, target = deployment
    result = sync(bot)
    assert result.returncode == 0, result.stderr
    assert (target / "f.txt").read_text() == "upstream\n"


def test_uncommitted_work_is_stashed_not_discarded(deployment):
    """A profile that commits in this checkout can have a story's work in the
    tree right now. Force-checking-out over it would destroy the only copy."""
    bot, target = deployment
    git("checkout", "-q", "-b", "story", cwd=target)
    (target / "f.txt").write_text("hours of work\n")

    assert sync(bot).returncode == 0

    assert (
        git("rev-parse", "--abbrev-ref", "HEAD", cwd=target).stdout.strip() == "master"
    )
    assert "hours of work\n" == git("show", "stash@{0}:f.txt", cwd=target).stdout


def test_a_stale_index_lock_does_not_stop_the_sync(deployment):
    """Left behind by a git process that was killed; every later git write
    fails on it until someone removes it by hand."""
    bot, target = deployment
    (target / ".git" / "index.lock").touch()

    assert sync(bot).returncode == 0
    assert not (target / ".git" / "index.lock").exists()


def test_a_truncated_loose_object_is_removed_so_the_fetch_can_refetch_it(deployment):
    """A zero-byte object is what a git write killed mid-flight leaves, and it
    fails every later read with "object file ... is empty"."""
    bot, target = deployment
    obj_dir = target / ".git" / "objects" / "ab"
    obj_dir.mkdir()
    truncated = obj_dir / "cdef0123456789abcdef0123456789abcdef01"
    truncated.touch()

    result = sync(bot)
    assert result.returncode == 0
    assert not truncated.exists()
    assert "Removing truncated git objects" in result.stdout


def test_a_ref_pointing_at_a_missing_commit_is_pruned(deployment):
    """One corrupt story branch fails `git fetch` outright ("bad object
    refs/heads/..."), and with it the sync and every job behind it."""
    bot, target = deployment
    (target / ".git" / "refs" / "heads" / "corrupt").write_text("de" * 20 + "\n")

    result = sync(bot)
    assert result.returncode == 0
    assert "Pruning corrupt ref: refs/heads/corrupt" in result.stdout
    assert not (target / ".git" / "refs" / "heads" / "corrupt").exists()


def test_an_unreachable_upstream_reports_and_still_exits_zero(deployment):
    """The script sits first in a cron `&&` chain. Exiting non-zero here
    cancels run.sh and review-prs -- the jobs that were the point of the run --
    and a stale checkout is a far smaller problem than a job that never ran."""
    bot, target = deployment
    git("remote", "set-url", "upstream", str(target.parent / "gone"), cwd=target)

    result = sync(bot)
    assert result.returncode == 0
    assert "WARNING: could not sync master" in result.stderr


def test_a_checkout_left_mid_rebase_is_returned_to_a_usable_state(deployment):
    """A killed run can leave the rebase in progress. Until it is aborted,
    `git checkout` refuses to move."""
    bot, target = deployment
    (target / "f.txt").write_text("theirs\n")
    git("add", "f.txt", cwd=target)
    git("commit", "-qm", "conflicting", cwd=target)
    git("checkout", "-q", "-b", "other", "HEAD~1", cwd=target)
    (target / "f.txt").write_text("ours\n")
    git("add", "f.txt", cwd=target)
    git("commit", "-qm", "also conflicting", cwd=target)
    subprocess.run(["git", "rebase", "master"], cwd=target, capture_output=True)
    assert (target / ".git" / "rebase-merge").exists() or (
        target / ".git" / "rebase-apply"
    ).exists()

    assert sync(bot).returncode == 0
    assert (
        git("rev-parse", "--abbrev-ref", "HEAD", cwd=target).stdout.strip() == "master"
    )


def test_a_checkout_git_cannot_read_at_all_still_exits_zero(deployment):
    """The worst case is the one that matters most: with HEAD gone git answers
    nothing about this directory, and exiting non-zero on it would cancel the
    job that was the point of the run."""
    bot, target = deployment
    (target / ".git" / "HEAD").unlink()

    result = sync(bot)
    assert result.returncode == 0, result.stderr
    assert "skipping upstream sync" in result.stdout
