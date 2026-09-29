"""scripts/timeout-tree.sh: the time limit, the quiet limit, and what it leaves behind.

Every run captures stdout through a pipe, the way run.sh tees a session into its
log: a process that outlives the script holding that pipe makes the run hang,
which subprocess's own timeout turns into a failure.
"""

import os
import subprocess
import time

ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPT = os.path.join(ROOT_DIR, "scripts", "timeout-tree.sh")


def timeout_tree(*args, poll=1, limit=20):
    env = dict(os.environ, TIMEOUT_TREE_POLL_SECS=str(poll))
    started = time.monotonic()
    result = subprocess.run(
        [SCRIPT, *map(str, args)],
        capture_output=True,
        text=True,
        env=env,
        timeout=limit,
    )
    return result, time.monotonic() - started


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def writer(path, times=8, gap=0.5):
    """A command that appends to `path` every `gap` seconds, then exits 0."""
    return [
        "bash",
        "-c",
        f'for _ in $(seq {times}); do echo x >> "$1"; sleep {gap}; done',
        "writer",
        str(path),
    ]


class TestTimeLimit:
    def test_the_command_exit_code_and_output_pass_through(self):
        result, _ = timeout_tree(60, "bash", "-c", "echo hi; exit 3")
        assert result.returncode == 3
        assert result.stdout == "hi\n"

    def test_the_watchdog_does_not_hold_the_pipe_after_the_command_ends(self):
        result, took = timeout_tree(3571, "true", limit=10)
        assert result.returncode == 0
        assert took < 5

    def test_a_command_past_the_limit_is_killed_with_its_children(self, tmp_path):
        """The kill goes leaf-first, so the command's last step is a foreground
        sleep: a bare `wait` there returns 0 once the child dies, and the shell
        can exit 0 before its own signal lands."""
        pidfile = tmp_path / "child"
        result, took = timeout_tree(
            1, "bash", "-c", f"sleep 3583 & echo $! > {pidfile}; sleep 3584"
        )
        assert result.returncode == 143
        assert "Job killed after exceeding timeout of 1s" in result.stdout
        assert took < 5
        assert not alive(int(pidfile.read_text()))

    def test_too_few_arguments_is_a_usage_error(self):
        result, _ = timeout_tree(60)
        assert result.returncode == 1
        assert "Usage:" in result.stdout


class TestQuietLimit:
    def test_nothing_matching_the_glob_is_a_stall(self, tmp_path):
        result, took = timeout_tree(
            "--quiet", 2, tmp_path / "none-*.jsonl", 60, "sleep", 30
        )
        assert result.returncode == 75
        assert "Job stopped after 2s with nothing written" in result.stdout
        assert took < 10

    def test_a_file_that_exists_but_does_not_change_is_a_stall(self, tmp_path):
        watched = tmp_path / "session.jsonl"
        watched.write_text("{}\n")
        result, _ = timeout_tree("--quiet", 2, watched, 60, "sleep", 30)
        assert result.returncode == 75

    def test_writes_to_a_watched_file_keep_the_command_alive(self, tmp_path):
        watched = tmp_path / "session.jsonl"
        watched.write_text("")
        result, took = timeout_tree("--quiet", 2, watched, 60, *writer(watched))
        assert result.returncode == 0
        assert took >= 3

    def test_writes_anywhere_under_a_watched_directory_count(self, tmp_path):
        watched = tmp_path / "session"
        (watched / "subagents").mkdir(parents=True)
        result, _ = timeout_tree(
            "--quiet", 2, watched, 60, *writer(watched / "subagents" / "a.jsonl")
        )
        assert result.returncode == 0

    def test_a_glob_is_expanded_again_on_every_poll(self, tmp_path):
        """A session's transcript does not exist until it writes its first line."""
        result, _ = timeout_tree(
            "--quiet", 2, tmp_path / "*.jsonl", 60, *writer(tmp_path / "late.jsonl")
        )
        assert result.returncode == 0

    def test_any_of_several_globs_counts(self, tmp_path):
        result, _ = timeout_tree(
            "--quiet",
            2,
            tmp_path / "none-*.jsonl",
            tmp_path / "other",
            60,
            *writer(tmp_path / "other"),
        )
        assert result.returncode == 0

    def test_a_path_with_a_space_in_it_is_watched(self, tmp_path):
        watched = tmp_path / "Application Support"
        watched.mkdir()
        result, _ = timeout_tree(
            "--quiet", 2, watched / "*.jsonl", 60, *writer(watched / "s.jsonl")
        )
        assert result.returncode == 0

    def test_the_time_limit_still_applies_to_a_busy_command(self, tmp_path):
        watched = tmp_path / "session.jsonl"
        result, took = timeout_tree(
            "--quiet", 100, watched, 2, *writer(watched, times=40)
        )
        assert result.returncode == 143
        assert "exceeding timeout of 2s" in result.stdout
        assert took < 10

    def test_a_command_that_ends_mid_poll_leaves_nothing_holding_the_pipe(
        self, tmp_path
    ):
        """The watchdog forks on every poll. Killed leaf-first mid-poll, it
        starts its sleep between the two kills, and that sleep held stdout, and
        run.sh's tee with it, for the whole poll."""
        result, took = timeout_tree(
            "--quiet", 60, tmp_path / "*.jsonl", 60, "true", poll=20, limit=15
        )
        assert result.returncode == 0
        assert took < 5
