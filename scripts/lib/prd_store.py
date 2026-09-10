"""Serialized access to data/prd.json.

Several processes write the PRD: every run's select-task.py, the agent's
update-prd-status.py, the backlog and bot-PR syncs, archive-prd.py. With one
run.sh that was a sequence; with several sharing a bot directory it is a
read-modify-write race, and the loser's update disappears.

Everything that changes the PRD goes through ``prd_lock``. The lock is an
exclusive flock on a sidecar file (data/.prd.lock) rather than on prd.json
itself, because an atomic save replaces prd.json's inode and would take the
lock with it. It is held by the kernel: a process killed mid-update releases
it, and there is no stale lock to clear.

Typical use::

    from lib.prd_store import mutate_prd

    def add_branch(prd):
        find(prd, "US-004")["branchName"] = "fix-thing"

    mutate_prd(prd_path, add_branch)

or, when the read and the write must span other work (selecting a story and
claiming it, say)::

    with prd_lock(prd_path):
        prd = load_prd(prd_path)
        ...
        save_prd(prd_path, prd)
"""

import contextlib
import fcntl
import json
import os
import tempfile

LOCK_FILENAME = ".prd.lock"


def bot_dir_for(prd_path, fallback=None):
    """The bot directory that owns this PRD.

    Everything keyed to a bot directory — run slots, claims — has to agree on
    which one it is. Deriving it from the PRD path rather than from the script
    location keeps a test (or a --prd pointing at a scratch copy) from reaching
    into the real deployment's claims and locks.
    """
    prd_path = os.path.abspath(prd_path)
    data_dir = os.path.dirname(prd_path)
    if os.path.basename(data_dir) == "data":
        return os.path.dirname(data_dir)
    return fallback or data_dir


def lock_path_for(prd_path):
    """Sidecar lock file that guards this PRD."""
    return os.path.join(os.path.dirname(os.path.abspath(prd_path)), LOCK_FILENAME)


@contextlib.contextmanager
def prd_lock(prd_path, timeout=120):
    """Hold an exclusive lock on the PRD for the body of the with-block.

    Blocks until the lock is free. ``timeout`` is a backstop against a wedged
    holder: the critical sections here are all sub-second, so waiting minutes
    means something is wrong and failing loudly beats hanging forever.
    """
    path = lock_path_for(prd_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Append mode: opening must never truncate a file another process is using.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        _flock_with_timeout(fd, path, timeout)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _flock_with_timeout(fd, path, timeout):
    import time

    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out after {timeout}s waiting for the PRD lock ({path}). "
                    f"Another run may be wedged — check ./scripts/run-status.sh"
                ) from None
            time.sleep(0.05)


def load_prd(prd_path):
    with open(prd_path) as f:
        return json.load(f)


def save_prd(prd_path, prd):
    """Replace the PRD atomically, so no reader ever sees a partial file."""
    dir_name = os.path.dirname(os.path.abspath(prd_path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(prd, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, prd_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def mutate_prd(prd_path, fn, timeout=120):
    """Read, apply ``fn(prd)``, and write back — all under one lock.

    Returns whatever ``fn`` returned. When ``fn`` returns the string "abort"
    nothing is written, which lets a caller validate against the freshly read
    state and back out without leaving the PRD changed.
    """
    with prd_lock(prd_path, timeout=timeout):
        prd = load_prd(prd_path)
        result = fn(prd)
        if result == "abort":
            return result
        save_prd(prd_path, prd)
        return result
