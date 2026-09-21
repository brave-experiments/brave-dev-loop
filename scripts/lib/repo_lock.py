"""The git-repo-lock.sh lock, for Python callers.

`scripts/git-repo-lock.sh` serializes git operations against a shared `.git`
directory, because two `git fetch`es into one repository collide on its ref
locks and one of them fails. Everything that fetches into the target repo has
to take *that* lock and not a lock of its own: a second mechanism guarding the
same repository serializes nothing.

The lock file is named the way git-repo-lock.sh names it — sha1 of the repo's
absolute path, first 12 characters — so a Python caller and a shell caller
contend for the same file. Change the naming here and you have to change it
there; `tests/test_scripts.py` asserts the two agree.

    from lib.repo_lock import repo_lock

    with repo_lock(target_repo_path):
        subprocess.run(["git", "-C", target_repo_path, "fetch", ...])

Waits rather than giving up — these are short operations and the caller cannot
skip one. See lib/file_lock.py for the lock itself.
"""

import hashlib
import os

from .file_lock import DEFAULT_TIMEOUT_S, file_lock

_BOT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)


def lock_path(repo_dir, bot_dir=None):
    """The lock file guarding `repo_dir`, as git-repo-lock.sh spells it."""
    repo_abs = os.path.abspath(repo_dir)
    # sha1 to match `shasum` in git-repo-lock.sh. It names a lock file; it is
    # not standing in for a signature.
    key = hashlib.sha1(repo_abs.encode()).hexdigest()[:12]  # nosemgrep
    return os.path.join(bot_dir or _BOT_DIR, ".ignore", f".git-{key}.lock")


def repo_lock(repo_dir, timeout=DEFAULT_TIMEOUT_S, bot_dir=None):
    """Hold the git lock for `repo_dir`, waiting up to `timeout` seconds."""
    return file_lock(
        lock_path(repo_dir, bot_dir),
        timeout=timeout,
        what=f"the git repository {os.path.abspath(repo_dir)}",
    )
