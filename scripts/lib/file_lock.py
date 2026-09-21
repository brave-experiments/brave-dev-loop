"""An advisory file lock for Python callers, matching lib/lock.sh's semantics.

`lib/lock.sh` is the shell side: flock on a file descriptor, held by the
kernel, so `kill -9` releases it and nothing is ever left stale. This is the
same lock for Python, and on the same files — `repo_lock` uses it to hold the
very file `git-repo-lock.sh` holds.

A fresh descriptor is opened per acquisition, so one lock serializes threads of
a single process as well as separate processes: flock belongs to the open file
description, and two descriptions of one file exclude each other no matter who
opened them.

Always lock a file that is never replaced. flock lives on the inode, so a
lock taken on a file that is then rewritten via `os.replace` guards nothing —
the next caller opens the new inode and takes an unrelated lock. Where the
guarded file is written atomically, lock a sentinel beside it instead
(`<name>.lock`), which is what `locked_json_update` does.
"""

import errno
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager

# Long enough for a cold fetch of a large repo to finish ahead of us, short
# enough that a wedged holder surfaces as an error instead of a hung run.
DEFAULT_TIMEOUT_S = 600


class FileLockTimeout(RuntimeError):
    """Nobody released the lock within the timeout."""


@contextmanager
def file_lock(path, timeout=DEFAULT_TIMEOUT_S, what=None):
    """Hold an exclusive lock on `path`, waiting up to `timeout` seconds.

    Waits rather than giving up: every caller here guards a short critical
    section it cannot skip. `what` names the thing being guarded, for the
    timeout message.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    # Append, never truncate: lock.sh records its owner pid in these files and
    # opening for write would erase the record of the run that holds it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(
                        f"Timed out after {timeout}s waiting for the lock on "
                        f"{what or path}. Check ./scripts/run-status.sh — another "
                        "run may be wedged."
                    ) from exc
                time.sleep(0.2)
        try:
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def locked_json_update(path, default=None, timeout=DEFAULT_TIMEOUT_S):
    """Read a JSON file, yield it for mutation, write it back atomically.

    The whole read-modify-write is one critical section, which is the only way
    two runs can both add an entry without one of them losing the other's: a
    process that reads before the other writes would otherwise save a copy of
    the file from before that write.

    The write goes to a temporary file and is renamed over the target, so a
    process killed mid-write leaves the previous contents rather than half a
    document. Callers treat unparseable JSON as absent, which without this
    would quietly discard the entire cache.

    Yields the mutable object. `default` (deep-copied by the caller if it is
    shared) is used when the file is missing or unreadable.
    """
    lock_file = f"{path}.lock"
    with file_lock(lock_file, timeout=timeout, what=path):
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {} if default is None else default

        yield data

        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".json-update-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
