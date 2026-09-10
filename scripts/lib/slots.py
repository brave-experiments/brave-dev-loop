"""Reading run-slot state from Python.

The shell side of this lives in scripts/lib/run-slots.sh and scripts/lib/lock.sh;
keep the two in step. Only the read side is here — slots are acquired by
run.sh, never by a Python helper.

The rule everything depends on: a slot is busy if and only if its lock is
held. Files left behind by a killed run prove nothing.
"""

import fcntl
import os


def slot_lockfile(bot_dir, slot):
    """Slot 1 keeps the pre-slots path, so a single-run deployment is unchanged."""
    slot = int(slot)
    if slot == 1:
        return os.path.join(bot_dir, ".run.lock")
    return os.path.join(bot_dir, f".run.slot-{slot}.lock")


def slot_run_state_file(bot_dir, slot):
    slot = int(slot)
    if slot == 1:
        return os.path.join(bot_dir, "data", "run-state.json")
    return os.path.join(bot_dir, "data", f"run-state.slot-{slot}.json")


def lock_owner_pid(lockfile):
    """The pid run.sh recorded when it took this lock, or None."""
    mkdir_pid = os.path.join(lockfile + ".d", "pid")
    for path in (mkdir_pid, lockfile):
        try:
            with open(path) as f:
                first = f.readline().strip()
        except OSError:
            continue
        if first.isdigit():
            return int(first)
    return None


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_is_held(lockfile):
    """Ask the kernel whether anything holds this lock.

    Called from a child of the run that holds it (select-task.py runs under
    run.sh), this correctly reports True: the parent's lock is on a different
    open file description, so our probe cannot take it.
    """
    mkdir_dir = lockfile + ".d"
    if os.path.isdir(mkdir_dir):
        # The mkdir fallback backend: its lock outlives a killed holder, so
        # liveness is the recorded pid.
        return _pid_alive(lock_owner_pid(lockfile))
    if not os.path.exists(lockfile):
        return False
    # Append mode: a probe must never truncate the owner record.
    fd = os.open(lockfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def slot_is_running(bot_dir, slot, pid=None):
    """Is a run live in this slot — and, when ``pid`` is given, *that* run?

    The pid check is what keeps a claim from outliving its run: after a run is
    killed and a new one takes the same slot, the lock is held again but by a
    different owner, so the dead run's claims are correctly seen as stale.
    """
    lockfile = slot_lockfile(bot_dir, slot)
    if not lock_is_held(lockfile):
        return False

    owner = lock_owner_pid(lockfile)
    if owner is None:
        # A lock file predating owner recording. Fall back to the claim's own
        # pid rather than declaring a live run's claim stale.
        return _pid_alive(pid) if pid is not None else True
    if pid is not None and owner != int(pid):
        return False
    # The lock can outlive its run: a child that inherited the lock fd keeps
    # the kernel lock while the run itself is gone (an orphaned slot — see
    # bot_lock_is_orphaned in lib/lock.sh). Nothing is working that story, so
    # the claim is stale. Pid reuse could in principle make a dead run look
    # alive here; the cost is one story skipped until the slot is reset.
    return _pid_alive(owner)
