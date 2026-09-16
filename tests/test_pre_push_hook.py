"""The signature half of hooks/pre-push, run the way git runs it."""

import os
import subprocess

import pytest

ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
HOOK = os.path.join(ROOT_DIR, "hooks", "pre-push")
ZEROS = "0" * 40


def git(repo, *args, **kwargs):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        **kwargs,
    )


def commit(repo, name, signed=True):
    (repo / name).write_text("x\n")
    git(repo, "add", name)
    args = ["commit", "-q", "-m", name]
    if not signed:
        args.insert(1, "--no-gpg-sign")
    git(repo, *args)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo configured as the bot signs, with the hook installed and one signed commit.

    Signing is configured before the first commit so that commit carries a signature too, and
    the key is generated beside its own public half so ssh-keygen finds the private one without
    an agent. Global config is redirected at a throwaway file because this machine's own
    commit.gpgsign would otherwise decide what these tests observe.
    """
    fake_global = tmp_path / "gitconfig"
    fake_global.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_global))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "user.name", "testbot")
    git(repo, "config", "user.email", "testbot@example.com")

    key = tmp_path / "signing_key"
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "testbot",
            "-f",
            str(key),
        ],
        check=True,
    )
    git(repo, "config", "gpg.format", "ssh")
    git(repo, "config", "user.signingkey", f"{key}.pub")
    git(repo, "config", "commit.gpgsign", "true")

    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(open(HOOK).read().replace("__BOT_USERNAME__", "testbot"))
    hook.chmod(0o755)

    commit(repo, "first.txt")
    return repo


def run_hook(repo, local_sha=None):
    """Run the installed hook as git would: from the work tree, a ref line on stdin."""
    if local_sha is None:
        local_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    return subprocess.run(
        ["bash", ".git/hooks/pre-push", "origin", "git@github.com:x/y.git"],
        cwd=str(repo),
        input=f"refs/heads/main {local_sha} refs/heads/main {ZEROS}\n",
        capture_output=True,
        text=True,
        # GH_TOKEN set means the hook trusts the caller to have pinned gh, which keeps these
        # tests off whichever account this machine happens to be logged in as.
        env={**os.environ, "GH_TOKEN": "stub"},
    )


def test_allows_a_signed_commit(repo):
    assert run_hook(repo).returncode == 0


def test_blocks_an_unsigned_commit_and_names_it(repo):
    # The failure this exists for: a repo configured to sign, and one commit made with
    # --no-gpg-sign because signing failed once. It is authored by the bot, so nothing else in
    # the hook objects to it, and GitHub reports it only as a badge on the commit.
    sha = commit(repo, "unsigned.txt", signed=False)

    result = run_hook(repo)

    assert result.returncode == 1
    assert sha[:7] in result.stderr


def test_ignores_an_unsigned_commit_already_on_a_remote(repo):
    # A branch rebased onto upstream work is not answerable for whether that work was signed.
    # Without this, a target repo whose history predates signing could never be pushed to.
    commit(repo, "theirs.txt", signed=False)
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    commit(repo, "mine.txt")

    assert run_hook(repo).returncode == 0


def test_names_the_base_the_whole_run_re_signs_onto(repo):
    # The message has to be a command somebody can run, and the base it needs is on screen
    # nowhere when the hook fires. With two unsigned commits it is the older one's parent, not
    # HEAD's, or the second rebase leaves the first commit exactly as it was.
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    commit(repo, "one.txt", signed=False)
    commit(repo, "two.txt", signed=False)

    result = run_hook(repo)

    assert f"git rebase -f --gpg-sign {base}" in result.stderr


def test_says_nothing_when_the_repo_does_not_sign(repo):
    # A repo nobody configured to sign has no signature to be missing. This is what lets the
    # hook ship to a checkout setup.sh has not touched.
    git(repo, "config", "--unset", "commit.gpgsign")
    commit(repo, "unsigned.txt", signed=False)

    assert run_hook(repo).returncode == 0


def test_allows_a_branch_deletion_in_a_signing_repo(repo):
    # An all-zero local sha creates no commits, so there is no signature to look for. Reading it
    # as a commit-ish would abort every `git push --delete`.
    assert run_hook(repo, local_sha=ZEROS).returncode == 0
