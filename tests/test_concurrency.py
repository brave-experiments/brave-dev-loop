"""Tests for running several run.sh instances from one bot directory.

Covers the four mechanisms that make it safe: run slots and their locks,
per-slot run state, story claims, and serialized writes to prd.json.

The lock tests spawn real processes and kill them, because that is the whole
point — a lock is only useful if it survives what actually happens to a run.
"""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SCRIPTS = os.path.join(ROOT, "scripts")
LIB = os.path.join(SCRIPTS, "lib")
SELECT_TASK = os.path.join(SCRIPTS, "select-task.py")
EXEC_CLEAN = os.path.join(SCRIPTS, "exec-clean.py")
UPDATE_PRD = os.path.join(SCRIPTS, "update-prd-status.py")

sys.path.insert(0, SCRIPTS)
from lib import claims as claims_lib  # noqa: E402
from lib import slots  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def bash(script, env=None, cwd=None):
    """Run a bash snippet with the lock/slot libraries already sourced."""
    full = f"source {LIB}/run-slots.sh\n{script}"
    return subprocess.run(
        ["bash", "-c", full],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=cwd,
    )


class Holder:
    """A background process holding a run slot, like a live run.sh does."""

    def __init__(self, bot_dir, max_slots=2, env=None, orphan_child=False):
        # orphan_child backgrounds the sleep so it inherits the lock fd and
        # outlives its parent — how a lock gets orphaned in the wild. Without
        # it bash execs the sleep, so the holder is a single process and
        # killing it drops the lock (the ordinary case).
        keep = "sleep 120 &\nwait" if orphan_child else "sleep 120"
        script = (
            f"source {LIB}/run-slots.sh\n"
            f"bot_acquire_run_slot {bot_dir} {max_slots} || exit 1\n"
            f'echo "$BOT_RUN_SLOT"\n'
            f"{keep}\n"
        )
        self.proc = subprocess.Popen(
            ["bash", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **(env or {})},
        )
        self.bot_dir = bot_dir
        self.slot = int(self.proc.stdout.readline().strip())
        self.pid = self.proc.pid

    def kill(self, children_too=True):
        with_children = _descendants(self.proc.pid) if children_too else []
        for pid in [self.proc.pid, *with_children]:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.proc.wait(timeout=10)
        _wait_for(lambda: not _pid_alive(self.proc.pid))

    def cleanup(self):
        try:
            self.kill()
        except Exception:
            pass


def _descendants(pid):
    out = subprocess.run(
        ["pgrep", "-P", str(pid)], capture_output=True, text=True
    ).stdout.split()
    kids = [int(p) for p in out if p.isdigit()]
    return kids + [g for k in kids for g in _descendants(k)]


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.fixture
def bot_dir(tmp_dir):
    """A minimal bot directory: data/, a PRD, and a run-state file per slot."""
    os.makedirs(os.path.join(tmp_dir, "data"), exist_ok=True)
    stories = [
        {"id": f"US-{i:03d}", "title": f"Story {i}", "status": "pending", "priority": i}
        for i in range(1, 6)
    ]
    with open(os.path.join(tmp_dir, "data", "prd.json"), "w") as f:
        json.dump({"stories": stories}, f, indent=2)
    for slot in (1, 2, 3):
        with open(slots.slot_run_state_file(tmp_dir, slot), "w") as f:
            json.dump({"runId": None, "storiesCheckedThisRun": []}, f)
    return tmp_dir


@pytest.fixture
def holders():
    made = []

    def _make(*args, **kwargs):
        h = Holder(*args, **kwargs)
        made.append(h)
        return h

    yield _make
    for h in made:
        h.cleanup()


def select(bot_dir, slot, pid, extra=()):
    result = subprocess.run(
        [
            sys.executable,
            SELECT_TASK,
            "--prd",
            os.path.join(bot_dir, "data", "prd.json"),
            "--run-state",
            slots.slot_run_state_file(bot_dir, slot),
            "--slot",
            str(slot),
            "--run-pid",
            str(pid),
            *extra,
        ],
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout or "{}")


# ═══════════════════════════════════════════════════════════════════════════
# Slot paths — slot 1 must keep every path it had before slots existed
# ═══════════════════════════════════════════════════════════════════════════


class TestSlotPaths:
    def test_slot_one_paths_are_unchanged(self):
        assert slots.slot_lockfile("/bot", 1) == "/bot/.run.lock"
        assert slots.slot_run_state_file("/bot", 1) == "/bot/data/run-state.json"

    def test_further_slots_get_their_own_files(self):
        assert slots.slot_lockfile("/bot", 2) == "/bot/.run.slot-2.lock"
        assert slots.slot_run_state_file("/bot", 3) == "/bot/data/run-state.slot-3.json"

    def test_shell_and_python_agree(self, tmp_dir):
        for slot in (1, 2, 7):
            out = bash(
                f"bot_slot_lockfile {tmp_dir} {slot}; bot_slot_run_state_file {tmp_dir} {slot}"
            ).stdout.split()
            assert out == [
                slots.slot_lockfile(tmp_dir, slot),
                slots.slot_run_state_file(tmp_dir, slot),
            ]


# ═══════════════════════════════════════════════════════════════════════════
# Locking
# ═══════════════════════════════════════════════════════════════════════════


class TestLocking:
    def test_second_run_cannot_take_a_held_slot(self, tmp_dir, holders):
        holders(tmp_dir, max_slots=1)
        out = bash(f"bot_acquire_run_slot {tmp_dir} 1; echo rc=$?")
        assert "rc=1" in out.stdout

    def test_slots_are_handed_out_in_order_then_exhausted(self, tmp_dir, holders):
        first = holders(tmp_dir, max_slots=2)
        second = holders(tmp_dir, max_slots=2)
        assert {first.slot, second.slot} == {1, 2}
        out = bash(f"bot_acquire_run_slot {tmp_dir} 2; echo rc=$?")
        assert "rc=1" in out.stdout

    def test_kill_releases_the_slot_immediately(self, tmp_dir, holders):
        holder = holders(tmp_dir, max_slots=1)
        holder.kill()
        out = bash(f"bot_lock_probe {slots.slot_lockfile(tmp_dir, 1)}; echo rc=$?")
        assert "rc=0" in out.stdout, "a killed run must not keep its slot"

    def test_freed_slot_is_reused(self, tmp_dir, holders):
        first = holders(tmp_dir, max_slots=2)
        holders(tmp_dir, max_slots=2)
        freed = first.slot
        first.kill()
        replacement = holders(tmp_dir, max_slots=2)
        assert replacement.slot == freed

    def test_owner_pid_is_recorded_and_survives_a_probe(self, tmp_dir, holders):
        holder = holders(tmp_dir, max_slots=1)
        lockfile = slots.slot_lockfile(tmp_dir, 1)
        assert slots.lock_owner_pid(lockfile) == holder.pid
        bash(f"bot_lock_probe {lockfile}")
        assert slots.lock_owner_pid(lockfile) == holder.pid

    def test_child_holding_the_fd_reads_as_orphaned_not_running(self, tmp_dir, holders):
        # A run killed while a child still holds its lock fd: the kernel lock
        # stays, but nothing is running. That must be tellable from a live run.
        holder = holders(tmp_dir, max_slots=1, orphan_child=True)
        lockfile = slots.slot_lockfile(tmp_dir, 1)
        holder.kill(children_too=False)
        assert bash(f"bot_lock_probe {lockfile}").returncode == 1, "lock still held"
        assert bash(f"bot_lock_is_orphaned {lockfile}").returncode == 0

    def test_running_slot_is_not_reported_as_orphaned(self, tmp_dir, holders):
        holders(tmp_dir, max_slots=1)
        lockfile = slots.slot_lockfile(tmp_dir, 1)
        assert bash(f"bot_lock_is_orphaned {lockfile}").returncode == 1

    def test_mkdir_backend_reclaims_a_lock_whose_owner_is_gone(self, tmp_dir, holders):
        env = {"BOT_LOCK_FORCE_BACKEND": "mkdir"}
        holder = holders(tmp_dir, max_slots=1, env=env)
        holder.kill()
        out = bash(f"bot_acquire_run_slot {tmp_dir} 1; echo rc=$?", env=env)
        assert "rc=0" in out.stdout
        assert "Clearing stale lock" in out.stderr

    def test_busy_report_names_the_holder(self, tmp_dir, holders):
        holder = holders(tmp_dir, max_slots=1)
        out = bash(
            f'bot_acquire_run_slot {tmp_dir} 1 || printf "%s" "$BOT_SLOT_BUSY_REPORT"'
        )
        assert f"held by pid {holder.pid}" in out.stdout


class TestFdInheritance:
    """A child that inherits the lock fd keeps the slot held after the run
    that owned it is gone. exec-clean.py is what stops that happening."""

    def _holds_lock(self, lockfile):
        return bash(f"bot_lock_probe {lockfile}").returncode == 1

    def test_a_plain_child_inherits_the_lock_fd(self, tmp_dir):
        # The failure mode, demonstrated: without sanitising, killing the
        # parent leaves the lock held by its child.
        lockfile = os.path.join(tmp_dir, "x.lock")
        proc = subprocess.Popen(
            [
                "bash",
                "-c",
                f"source {LIB}/lock.sh\n"
                f'bot_acquire_lock "{lockfile}" || exit 1\n'
                "sleep 60 &\nwait\n",
            ]
        )
        _wait_for(lambda: os.path.exists(lockfile) and self._holds_lock(lockfile))
        kids = _descendants(proc.pid)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        try:
            assert self._holds_lock(lockfile), "expected the child to hold it"
        finally:
            for pid in kids:
                with __import__("contextlib").suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)

    def test_exec_clean_child_does_not_inherit_the_lock_fd(self, tmp_dir):
        lockfile = os.path.join(tmp_dir, "y.lock")
        proc = subprocess.Popen(
            [
                "bash",
                "-c",
                f"source {LIB}/lock.sh\n"
                f'bot_acquire_lock "{lockfile}" || exit 1\n'
                f"{sys.executable} {EXEC_CLEAN} sleep 60 &\nwait\n",
            ]
        )
        _wait_for(lambda: os.path.exists(lockfile) and self._holds_lock(lockfile))
        kids = _descendants(proc.pid)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        try:
            assert _wait_for(lambda: not self._holds_lock(lockfile)), (
                "the lock must be free once its owner dies"
            )
        finally:
            for pid in kids:
                with __import__("contextlib").suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)

    def test_exec_clean_runs_the_command_and_forwards_status(self, tmp_dir):
        ok = subprocess.run(
            [sys.executable, EXEC_CLEAN, "--cd", tmp_dir, "pwd"],
            capture_output=True,
            text=True,
        )
        assert os.path.realpath(ok.stdout.strip()) == os.path.realpath(tmp_dir)
        bad = subprocess.run([sys.executable, EXEC_CLEAN, "false"], capture_output=True)
        assert bad.returncode == 1
        missing = subprocess.run(
            [sys.executable, EXEC_CLEAN, "no-such-command-here"], capture_output=True
        )
        assert missing.returncode == 127


# ═══════════════════════════════════════════════════════════════════════════
# The concurrency gate
# ═══════════════════════════════════════════════════════════════════════════


class TestConcurrencyGate:
    @pytest.mark.parametrize(
        "max_runs,worktrees,allowed",
        [
            ("1", "false", True),  # today's brave-core deployments
            ("1", "true", True),
            ("2", "true", True),
            ("2", "false", False),  # would share one checkout
            ("0", "true", False),
            ("abc", "true", False),
        ],
    )
    def test_gate(self, max_runs, worktrees, allowed):
        out = bash(f"bot_validate_concurrency {max_runs} someprofile {worktrees}")
        assert (out.returncode == 0) is allowed

    def test_refusal_says_what_to_change(self):
        out = bash("bot_validate_concurrency 2 brave-core false")
        assert "does not use worktrees" in out.stderr
        assert "projects/brave-core/profile.json" in out.stderr


# ═══════════════════════════════════════════════════════════════════════════
# Claims
# ═══════════════════════════════════════════════════════════════════════════


class TestClaims:
    def test_claim_counts_only_while_the_slot_is_held(self, bot_dir, holders):
        holder = holders(bot_dir, max_slots=2)
        claims_lib.claim(bot_dir, "US-001", holder.slot, holder.pid)
        assert "US-001" in claims_lib.active(bot_dir)
        holder.kill()
        assert claims_lib.active(bot_dir) == {}

    def test_claim_from_a_slot_nobody_holds_is_ignored(self, bot_dir):
        claims_lib.claim(bot_dir, "US-001", 1, 999999)
        assert claims_lib.active(bot_dir) == {}

    def test_claim_does_not_survive_the_slot_being_reused(self, bot_dir, holders):
        first = holders(bot_dir, max_slots=1)
        claims_lib.claim(bot_dir, "US-001", first.slot, first.pid)
        first.kill()
        second = holders(bot_dir, max_slots=1)
        assert second.slot == first.slot
        assert claims_lib.active(bot_dir) == {}, "a new run inherits no claims"

    def test_release_drops_only_the_named_story(self, bot_dir, holders):
        holder = holders(bot_dir, max_slots=1)
        claims_lib.claim(bot_dir, "US-001", holder.slot, holder.pid)
        claims_lib.claim(bot_dir, "US-002", holder.slot, holder.pid)
        claims_lib.release(bot_dir, story_id="US-001", slot=holder.slot)
        assert set(claims_lib.active(bot_dir)) == {"US-002"}

    def test_a_second_run_cannot_claim_a_held_story(self, bot_dir, holders):
        first = holders(bot_dir, max_slots=2)
        second = holders(bot_dir, max_slots=2)
        assert claims_lib.claim(bot_dir, "US-001", first.slot, first.pid)
        assert not claims_lib.claim(bot_dir, "US-001", second.slot, second.pid)


# ═══════════════════════════════════════════════════════════════════════════
# Selection
# ═══════════════════════════════════════════════════════════════════════════


class TestConcurrentSelection:
    def test_two_runs_never_select_the_same_story(self, bot_dir, holders):
        first = holders(bot_dir, max_slots=2)
        second = holders(bot_dir, max_slots=2)
        a = select(bot_dir, first.slot, first.pid)
        b = select(bot_dir, second.slot, second.pid)
        assert a["selected"] and b["selected"]
        assert a["storyId"] != b["storyId"]

    def test_selection_claims_the_story(self, bot_dir, holders):
        holder = holders(bot_dir, max_slots=1)
        picked = select(bot_dir, holder.slot, holder.pid)["storyId"]
        assert set(claims_lib.active(bot_dir)) == {picked}

    def test_parallel_selections_are_all_distinct(self, bot_dir, holders):
        # Three runs selecting at once, from a PRD of five stories.
        hs = [holders(bot_dir, max_slots=3) for _ in range(3)]
        with_pool = [
            subprocess.Popen(
                [
                    sys.executable,
                    SELECT_TASK,
                    "--prd",
                    os.path.join(bot_dir, "data", "prd.json"),
                    "--run-state",
                    slots.slot_run_state_file(bot_dir, h.slot),
                    "--slot",
                    str(h.slot),
                    "--run-pid",
                    str(h.pid),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            for h in hs
        ]
        picked = [json.loads(p.communicate()[0])["storyId"] for p in with_pool]
        assert len(set(picked)) == 3, f"overlapping selections: {picked}"

    def test_story_becomes_available_again_when_its_run_dies(self, bot_dir, holders):
        first = holders(bot_dir, max_slots=2)
        picked = select(bot_dir, first.slot, first.pid)["storyId"]
        first.kill()
        second = holders(bot_dir, max_slots=2)
        # Only the dead run's story is left unchecked in slot 2's run state.
        state_path = slots.slot_run_state_file(bot_dir, second.slot)
        with open(state_path, "w") as f:
            others = [f"US-{i:03d}" for i in range(1, 6) if f"US-{i:03d}" != picked]
            json.dump({"runId": None, "storiesCheckedThisRun": others}, f)
        assert select(bot_dir, second.slot, second.pid)["storyId"] == picked


# ═══════════════════════════════════════════════════════════════════════════
# prd.json writes
# ═══════════════════════════════════════════════════════════════════════════


class TestPrdWrites:
    def test_parallel_updates_do_not_lose_writes(self, bot_dir):
        prd_path = os.path.join(bot_dir, "data", "prd.json")
        run_state = slots.slot_run_state_file(bot_dir, 1)
        procs = [
            subprocess.Popen(
                [
                    sys.executable,
                    UPDATE_PRD,
                    "--prd",
                    prd_path,
                    "--run-state",
                    run_state,
                    "set-branch",
                    f"US-{i:03d}",
                    "--branch",
                    f"branch-{i}",
                ],
                stdout=subprocess.DEVNULL,
            )
            for i in range(1, 6)
        ]
        for p in procs:
            assert p.wait() == 0
        with open(prd_path) as f:
            prd = json.load(f)
        assert all(s.get("branchName") for s in prd["stories"])

    def test_the_file_is_never_left_partially_written(self, bot_dir):
        # An interrupted write must not truncate the PRD: it is replaced by
        # rename, so a reader sees either the old file or the new one.
        prd_path = os.path.join(bot_dir, "data", "prd.json")
        run_state = slots.slot_run_state_file(bot_dir, 1)
        for i in range(1, 4):
            subprocess.run(
                [
                    sys.executable,
                    UPDATE_PRD,
                    "--prd",
                    prd_path,
                    "--run-state",
                    run_state,
                    "set-branch",
                    f"US-{i:03d}",
                    "--branch",
                    f"b{i}",
                ],
                capture_output=True,
            )
            with open(prd_path) as f:
                json.load(f)  # raises if a partial write ever lands
