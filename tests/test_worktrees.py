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
import time

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


def backdate(repo, worktree, hours):
    """Make a worktree look untouched for `hours`.

    Both halves of what idleness is read from: the `.git` file `worktree add`
    wrote, and the administrative directory every later git command writes into.
    """
    when = time.time() - hours * 3600
    admin = repo / ".git" / "worktrees" / worktree.name
    for path in [worktree / ".git", admin, *admin.iterdir()]:
        os.utime(path, (when, when))


def install_post_checkout(repo, user=BOT_USER):
    """Install the hook the way scripts/setup.sh does, substitution included.

    Into the directory core.hooksPath names where the repo sets one, because that setting replaces
    .git/hooks rather than adding to it — a hook written to .git/hooks in such a repo never runs.
    """
    # `git config` of an unset key exits 1, which is an answer here rather than a failure.
    configured = subprocess.run(
        ["git", "config", "--path", "core.hooksPath"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    hooks = repo / configured if configured else repo / ".git" / "hooks"
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


# ── Provisioning: the hooks a relative core.hooksPath hides from a worktree ───


def use_relative_hooks_path(repo, name=".githooks"):
    """Configure the repo the way a target repo with its own checked-in hooks does.

    `git config core.hooksPath .githooks` is the spelling that matters, because git resolves a
    relative path against each working tree's own top level rather than once for the repo.
    """
    (repo / name).mkdir(exist_ok=True)
    git("config", "core.hooksPath", name, cwd=repo)
    return repo / name


def test_a_worktree_gets_the_untracked_hooks_a_relative_path_would_hide(repo):
    """The gap that let an unsigned commit reach a pull request.

    `git worktree add` writes only what the repo tracks, and every hook setup installs is
    untracked — so with a relative core.hooksPath the new tree looks for hooks in its own empty
    copy of that directory and pushes with none of them.
    """
    hooks = use_relative_hooks_path(repo)
    (hooks / "pre-push").write_text("#!/bin/bash\nexit 1\n")
    (hooks / "pre-push").chmod(0o755)
    install_post_checkout(repo)

    worktree = add_worktree(repo, "repo-133", branch="fix-133")

    assert (worktree / ".githooks" / "pre-push").exists()
    assert os.access(worktree / ".githooks" / "pre-push", os.X_OK)


def test_a_hook_the_repo_tracks_is_left_as_its_author_committed_it(repo):
    """A committed hook is already in the worktree and is not ours to replace."""
    hooks = use_relative_hooks_path(repo)
    (hooks / "pre-commit").write_text("#!/bin/bash\n# theirs\nexit 0\n")
    (hooks / "pre-commit").chmod(0o755)
    git("add", "-f", ".githooks/pre-commit", cwd=repo)
    git("commit", "-qm", "their hook", cwd=repo)
    # What the main checkout happens to hold now differs from what it committed.
    (hooks / "pre-commit").write_text("#!/bin/bash\n# local edit\nexit 0\n")
    install_post_checkout(repo)

    worktree = add_worktree(repo, "repo-133", branch="fix-133")

    assert "theirs" in (worktree / ".githooks" / "pre-commit").read_text()


def test_an_absolute_hooks_path_is_left_alone(repo):
    """Already shared by every worktree, so there is nothing to copy and no directory to make."""
    hooks = repo / "shared-hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/bash\nexit 1\n")
    git("config", "core.hooksPath", str(hooks), cwd=repo)
    install_post_checkout(repo)

    worktree = add_worktree(repo, "repo-133", branch="fix-133")

    assert not (worktree / "shared-hooks").exists()


def test_a_repo_that_never_moved_its_hooks_grows_no_directory(repo):
    """Unset means .git/hooks, which a worktree already shares. Copying would invent a tree file."""
    install_post_checkout(repo)

    worktree = add_worktree(repo, "repo-133", branch="fix-133")

    assert git("status", "--porcelain", cwd=worktree).stdout.strip() == ""


def refresh_hooks(repo, hooks_src, user=BOT_USER):
    """Run the refresh a run does at start, returning what it reported."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{LIB}/repo-hooks.sh"\n'
            f'repo_refresh_bot_hooks "{repo}" "{user}" "{hooks_src}"\n',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_worktree_that_already_existed_gets_a_hook_added_later(tmp_path, repo):
    """post-checkout fires once, so a worktree predating a hook never sees it without this.

    Stories outlive a run and their worktrees are re-entered, so this is the ordinary case: the
    worktree brave/bravebot#641 was pushed unsigned from was made before the guard reached it.
    """
    use_relative_hooks_path(repo)
    install_post_checkout(repo)
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    (worktree / ".githooks" / "pre-push").unlink(missing_ok=True)

    hooks_src = tmp_path / "bot-hooks"
    hooks_src.mkdir()
    (hooks_src / "pre-push").write_text("#!/bin/bash\n# __BOT_USERNAME__\nexit 1\n")

    refresh_hooks(repo, hooks_src)

    installed = worktree / ".githooks" / "pre-push"
    assert installed.exists()
    assert BOT_USER in installed.read_text()


def test_refreshing_a_worktree_that_is_current_reports_nothing(tmp_path, repo):
    """Run at every start, so anything it says on a healthy repo is noise nobody reads."""
    use_relative_hooks_path(repo)
    install_post_checkout(repo)
    add_worktree(repo, "repo-133", branch="fix-133")

    hooks_src = tmp_path / "bot-hooks"
    hooks_src.mkdir()
    (hooks_src / "pre-push").write_text("#!/bin/bash\n# __BOT_USERNAME__\nexit 1\n")

    refresh_hooks(repo, hooks_src)

    assert refresh_hooks(repo, hooks_src) == ""


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


def test_commits_no_remote_has_do_not_keep_a_worktree(repo, bot_dir):
    """A story committed but not pushed: the worktree goes, the commits do not.

    The branch is the repository's rather than the worktree's, so removal parks
    the work under the same name instead of losing it, and the report says which
    name so it can be found again.
    """
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    (worktree / "fix.txt").write_text("the fix\n")
    git("add", "fix.txt", cwd=worktree)
    git("commit", "-qm", "the fix", cwd=worktree)
    head = git("rev-parse", "HEAD", cwd=worktree).stdout.strip()

    report = clean(repo, bot_dir)

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()
    assert git("rev-parse", "fix-133", cwd=repo).stdout.strip() == head
    assert report["unpushed"] == [{"path": str(worktree), "branch": "fix-133"}]


def test_a_squash_merged_worktree_is_collected(repo, bot_dir):
    """The shape that used to make every finished worktree permanent.

    A squash merge puts the change on the base branch as a new commit and the
    head branch is deleted with it, so the worktree's own commits are on no
    remote and are ancestors of nothing. That is what every landed story looks
    like, and asking whether a remote held them kept the lot.
    """
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    (worktree / "fix.txt").write_text("the fix\n")
    git("add", "fix.txt", cwd=worktree)
    git("commit", "-qm", "the fix", cwd=worktree)
    git("push", "-q", "origin", "fix-133", cwd=worktree)

    git("merge", "-q", "--squash", "fix-133", cwd=repo)
    git("commit", "-qm", "the fix (#1)", cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    git("push", "-q", "origin", "--delete", "fix-133", cwd=repo)
    git("fetch", "-q", "--prune", "origin", cwd=repo)

    report = clean(repo, bot_dir)

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()


def test_a_detached_worktree_holding_commits_nothing_names_is_kept(repo, bot_dir):
    """No branch to park them on, so pruning the entry is what loses them."""
    worktree = add_worktree(repo, "repo-133")
    (worktree / "fix.txt").write_text("the fix\n")
    git("add", "fix.txt", cwd=worktree)
    git("commit", "-qm", "the fix", cwd=worktree)

    report = clean(repo, bot_dir)

    assert report["removed"] == []
    assert worktree.exists()
    assert "detached and no ref keeps its commits" in report["kept"][0]["reason"]


def test_a_detached_worktree_a_ref_still_covers_is_collected(repo, bot_dir):
    """The control: detached is not itself the thing worth keeping a worktree for."""
    worktree = add_worktree(repo, "repo-133")

    report = clean(repo, bot_dir)

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()


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
    assert report["kept"][0]["reason"] == "it was used too recently"


def test_max_age_hours_collects_one_that_is_old_enough(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)
    backdate(repo, worktree, hours=48)

    report = clean(repo, bot_dir, "--max-age-hours", "24")

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()


def test_max_age_hours_keeps_an_old_worktree_git_just_ran_in(repo, bot_dir):
    """A long-lived story's worktree, between two of its iterations.

    Days old, pushed, nothing uncommitted and no claim held: every other check
    would let it go, and the run that is still working in it would lose the tree
    it built. Age is measured from the last git command, not from `worktree add`.
    """
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)
    backdate(repo, worktree, hours=48)
    git("status", "--porcelain", cwd=worktree)

    report = clean(repo, bot_dir, "--max-age-hours", "24")

    assert report["removed"] == []
    assert worktree.exists()


def test_looking_at_a_worktree_does_not_count_as_using_it(repo, bot_dir):
    """The pass at the start of every run must not make the next one a no-op.

    Deciding whether a worktree holds work means running git in it, and git
    leaves a lock file behind in the worktree's administrative directory as it
    goes. Count that and one run of this script marks every worktree as busy for
    the next day, so the 24-hour pass would never collect anything again.
    """
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)
    backdate(repo, worktree, hours=48)

    first = clean(repo, bot_dir, "--max-age-hours", "24", "--dry-run")
    second = clean(repo, bot_dir, "--max-age-hours", "24", "--dry-run")

    assert first["removed"] == [str(worktree)]
    assert second["removed"] == [str(worktree)]


def test_all_takes_a_worktree_holding_uncommitted_changes(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)
    (worktree / "scratch.txt").write_text("half a fix\n")

    report = clean(repo, bot_dir, "--all")

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()
    assert "uncommitted changes" in report["lostWork"][0]["reason"]
    assert report["lostWork"][0]["branch"] == "fix-133"


def test_all_takes_a_recently_used_worktree(repo, bot_dir):
    """--all is for emptying the directory, so the age floor does not apply."""
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("push", "-q", "origin", "fix-133", cwd=worktree)
    git("fetch", "-q", "origin", cwd=repo)

    report = clean(repo, bot_dir, "--all", "--max-age-hours", "24")

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()


def test_all_keeps_the_unpushed_commits_it_removes_the_worktree_for(repo, bot_dir):
    """What --all can lose is uncommitted changes, and only those.

    A branch belongs to the repository rather than to the worktree checked out
    on it, so commits no remote has are still there afterwards.
    """
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    (worktree / "fix.txt").write_text("the fix\n")
    git("add", "fix.txt", cwd=worktree)
    git("commit", "-qm", "the fix", cwd=worktree)
    head = git("rev-parse", "HEAD", cwd=worktree).stdout.strip()

    report = clean(repo, bot_dir, "--all")

    assert report["removed"] == [str(worktree)]
    assert not worktree.exists()
    assert git("rev-parse", "fix-133", cwd=repo).stdout.strip() == head


def test_all_still_keeps_a_worktree_a_live_run_claims(repo, bot_dir, live_run):
    """Deleting the directory under a running session breaks it, all or not."""
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

    report = clean(repo, bot_dir, "--all")

    assert report["removed"] == []
    assert worktree.exists()


def test_all_still_keeps_a_worktree_git_has_locked(repo, bot_dir):
    worktree = add_worktree(repo, "repo-133", branch="fix-133")
    git("worktree", "lock", str(worktree), cwd=repo)

    report = clean(repo, bot_dir, "--all")

    assert report["removed"] == []
    assert worktree.exists()
    assert report["kept"][0]["reason"] == "git has it locked"


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
