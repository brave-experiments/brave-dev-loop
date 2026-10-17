"""bot.concurrentJobs: one ./run.sh that starts several runs of itself.

The lib is driven with a stand-in for run.sh, since what it owns is the starting,
the output and the stopping; run.sh's own wiring is pinned at the end and was
exercised against tests/sim by hand.
"""

import json
import os
import shutil
import signal
import subprocess
import textwrap
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
LIB = os.path.join(ROOT, "scripts", "lib", "parallel-jobs.sh")
RUN_SH = os.path.join(ROOT, "run.sh")


@pytest.fixture(autouse=True)
def no_start_delay(monkeypatch):
    # The default gap between jobs is 10s; tests that are not about it run without.
    monkeypatch.setenv("BOT_JOB_START_DELAY_SECONDS", "0")


def bash(script, **kwargs):
    return subprocess.run(
        ["bash", "-c", f"source {LIB}\n{script}"],
        capture_output=True,
        text=True,
        timeout=60,
        **kwargs,
    )


def worker(tmp_path, body):
    path = tmp_path / "worker.sh"
    path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)


class TestValidate:
    @pytest.mark.parametrize("jobs,slots", [("1", "1"), ("2", "4"), ("4", "4")])
    def test_a_count_the_slots_can_hold_is_accepted(self, jobs, slots):
        assert bash(f"bot_validate_concurrent_jobs {jobs} {slots}").returncode == 0

    @pytest.mark.parametrize("jobs", ["0", "-1", "x", "1.5", ""])
    def test_a_value_that_is_not_a_positive_count_is_refused(self, jobs):
        out = bash(f"bot_validate_concurrent_jobs '{jobs}' 4")
        assert out.returncode == 1
        assert "bot.concurrentJobs must be" in out.stderr

    def test_more_jobs_than_slots_says_which_setting_to_raise(self):
        out = bash("bot_validate_concurrent_jobs 5 3")
        assert out.returncode == 1
        assert "2 of them would find every slot taken" in out.stderr
        assert "Raise bot.maxConcurrentRuns to at least 5" in out.stderr


class TestRunParallelJobs:
    def run(self, tmp_path, body, jobs=3, args="5 --agent codex", **kwargs):
        script = worker(tmp_path, body)
        return bash(f"bot_run_parallel_jobs {jobs} {script} {args}", **kwargs)

    def test_every_job_is_started_with_the_arguments_it_was_given(self, tmp_path):
        out = self.run(tmp_path, 'echo "job=$BOT_RUN_JOB args=$*"')
        assert out.returncode == 0, out.stderr
        lines = out.stdout.splitlines()
        for i in (1, 2, 3):
            assert f"[job {i}] job={i} args=5 --agent codex" in lines

    def test_the_jobs_run_at_the_same_time(self, tmp_path):
        # Each job waits until all of them have started. One at a time, the first
        # would wait out its limit and give up.
        marks = tmp_path / "marks"
        marks.mkdir()
        out = self.run(
            tmp_path,
            f"""
            touch {marks}/$BOT_RUN_JOB
            for _ in $(seq 1 100); do
              [ "$(ls {marks} | wc -l)" -ge 3 ] && exit 0
              sleep 0.1
            done
            exit 9
            """,
        )
        assert out.returncode == 0, out.stdout

    def test_stderr_is_prefixed_too(self, tmp_path):
        out = self.run(tmp_path, 'echo "bad news" >&2', jobs=2)
        assert "[job 1] bad news" in out.stdout
        assert "[job 2] bad news" in out.stdout

    def test_an_unterminated_last_line_is_not_lost(self, tmp_path):
        out = self.run(tmp_path, 'printf "no newline"', jobs=1)
        assert "[job 1] no newline" in out.stdout

    def test_a_line_is_never_split_by_another_jobs(self, tmp_path):
        out = self.run(
            tmp_path,
            'for i in $(seq 1 200); do echo "line $i of job $BOT_RUN_JOB"; done',
            jobs=3,
        )
        lines = out.stdout.splitlines()
        body = [ln for ln in lines if ln.startswith("[job ")]
        assert len(body) == 600
        for ln in body:
            job = ln[len("[job ") : len("[job ") + 1]
            assert ln.endswith(f"of job {job}"), ln

    def test_it_exits_with_the_worst_status_any_job_returned(self, tmp_path):
        out = self.run(
            tmp_path, "exit $((BOT_RUN_JOB == 2 ? 3 : (BOT_RUN_JOB == 3 ? 1 : 0)))"
        )
        assert out.returncode == 3

    def test_all_jobs_succeeding_exits_zero(self, tmp_path):
        assert self.run(tmp_path, "exit 0").returncode == 0

    def test_a_job_does_not_title_the_terminal(self, tmp_path):
        out = self.run(tmp_path, 'echo "tty=$BOT_TITLE_TTY"', jobs=1)
        assert "[job 1] tty=/dev/null" in out.stdout

    def test_it_says_how_many_jobs_it_starts(self, tmp_path):
        out = self.run(tmp_path, "true", jobs=2)
        assert "Starting 2 parallel jobs" in out.stdout

    def test_it_leaves_no_pipes_behind(self, tmp_path):
        tmpdir = tmp_path / "tmp"
        tmpdir.mkdir()
        self.run(tmp_path, "true", env={**os.environ, "TMPDIR": str(tmpdir)})
        assert list(tmpdir.iterdir()) == []


class TestStartDelay:
    # whole-second timestamps: a 2s gap is at least 1s apart however they round
    def test_each_job_starts_after_the_one_before_it(self, tmp_path):
        starts = tmp_path / "starts"
        script = worker(tmp_path, f'echo "$BOT_RUN_JOB $(date +%s)" >> {starts}')
        out = bash(
            f"bot_run_parallel_jobs 2 {script}",
            env={**os.environ, "BOT_JOB_START_DELAY_SECONDS": "2"},
        )
        assert out.returncode == 0, out.stderr
        at = {}
        for line in starts.read_text().splitlines():
            job, ts = line.split()
            at[int(job)] = int(ts)
        assert at[2] - at[1] >= 1

    def test_the_default_is_ten_seconds(self):
        out = bash(
            'echo "$BOT_JOB_START_DELAY_SECONDS"',
            env={
                k: v
                for k, v in os.environ.items()
                if k != "BOT_JOB_START_DELAY_SECONDS"
            },
        )
        assert out.stdout.strip() == "10"

    def test_it_says_so(self, tmp_path):
        out = bash(
            f"bot_run_parallel_jobs 2 {worker(tmp_path, 'true')}",
            env={**os.environ, "BOT_JOB_START_DELAY_SECONDS": "1"},
        )
        assert "Each job starts 1s after the one before it." in out.stdout

    def test_a_single_job_neither_waits_nor_says_so(self, tmp_path):
        began = time.monotonic()
        out = bash(
            f"bot_run_parallel_jobs 1 {worker(tmp_path, 'true')}",
            env={**os.environ, "BOT_JOB_START_DELAY_SECONDS": "30"},
        )
        assert time.monotonic() - began < 10
        assert "after the one before it" not in out.stdout

    def test_a_signal_during_the_gap_stops_the_started_jobs_at_once(self, tmp_path):
        marker = f"{4000 + os.getpid() % 1000}.{int(time.time()) % 100:02d}"
        script = worker(
            tmp_path,
            f"""
            touch {tmp_path}/started-$BOT_RUN_JOB
            sleep {marker}
            """,
        )
        proc = subprocess.Popen(
            ["bash", "-c", f"source {LIB}\nbot_run_parallel_jobs 3 {script}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "BOT_JOB_START_DELAY_SECONDS": "30"},
        )
        try:
            for _ in range(100):
                if (tmp_path / "started-1").exists():
                    break
                time.sleep(0.1)
            else:
                pytest.fail("the first job never started")
            began = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(timeout=25) == 130
            assert time.monotonic() - began < 20
        finally:
            if proc.poll() is None:
                proc.kill()
        assert not (tmp_path / "started-2").exists()
        survivors = subprocess.run(
            ["pgrep", "-f", f"sleep {marker}"], capture_output=True, text=True
        )
        assert survivors.stdout.strip() == "", "a job's child outlived the stop"
        assert "Stopping 1 jobs" in proc.stdout.read()


class TestStopping:
    def test_a_signal_stops_every_job_and_everything_under_it(self, tmp_path):
        # A job is several processes deep: its shell, then the agent. A unique
        # sleep duration stands in for the agent, so a survivor is findable.
        marker = f"{3000 + os.getpid() % 1000}.{int(time.time()) % 100:02d}"
        script = worker(
            tmp_path,
            f"""
            touch {tmp_path}/started-$BOT_RUN_JOB
            sleep {marker}
            """,
        )
        proc = subprocess.Popen(
            ["bash", "-c", f"source {LIB}\nbot_run_parallel_jobs 2 {script}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            for _ in range(100):
                if len(list(tmp_path.glob("started-*"))) == 2:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("the jobs never started")
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(timeout=30) == 130
        finally:
            if proc.poll() is None:
                proc.kill()
        survivors = subprocess.run(
            ["pgrep", "-f", f"sleep {marker}"], capture_output=True, text=True
        )
        assert survivors.stdout.strip() == "", "a job's child outlived the stop"
        assert "Stopping 2 jobs" in proc.stdout.read()

    def test_a_job_that_ignores_term_is_killed_after_the_grace_period(self, tmp_path):
        script = worker(
            tmp_path,
            f"""
            trap '' TERM
            echo $$ > {tmp_path}/pid
            while :; do sleep 0.2; done
            """,
        )
        proc = subprocess.Popen(
            ["bash", "-c", f"source {LIB}\nbot_run_parallel_jobs 1 {script}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "BOT_JOB_STOP_SECONDS": "2"},
        )
        try:
            for _ in range(100):
                if (tmp_path / "pid").exists():
                    break
                time.sleep(0.1)
            else:
                pytest.fail("the job never started")
            pid = int((tmp_path / "pid").read_text())
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(timeout=30) == 130
        finally:
            if proc.poll() is None:
                proc.kill()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert "did not stop within 2s; killing it" in proc.stdout.read()


class TestRunSh:
    @staticmethod
    def body():
        with open(RUN_SH) as f:
            return f.read()

    def test_the_supervisor_runs_before_a_slot_is_taken(self):
        body = self.body()
        assert body.index("bot_run_parallel_jobs") < body.index("bot_acquire_run_slot")

    def test_a_job_does_not_start_more_jobs(self):
        assert 'if [ -z "${BOT_RUN_JOB:-}" ] && [ "$BOT_CONCURRENT_JOBS" -gt 1 ]' in (
            self.body()
        )

    def test_tui_runs_one_job_and_says_so(self):
        body = self.body()
        start = body.index('if [ -z "${BOT_RUN_JOB:-}"')
        block = body[start : body.index("\nfi\n", start)]
        assert 'if [ "$USE_TUI" = true ]' in block
        assert block.index("USE_TUI") < block.index("bot_run_parallel_jobs")
        assert "running one job" in block

    def test_the_setting_is_validated_against_the_slots(self):
        assert (
            'bot_validate_concurrent_jobs "$BOT_CONCURRENT_JOBS" '
            '"$BOT_MAX_CONCURRENT_RUNS"'
        ) in self.body()


class TestUrlRunsOneJob:
    """run.sh's own argument handling, against a bot directory of stub libs.

    The stubs stop run.sh where it would start the supervisor ("PARALLEL") or take
    its slot ("SINGLE"), which is the fork this decides.
    """

    URL = "https://github.com/o/r/issues/613"

    STUBS = {
        "run-slots.sh": """
            bot_validate_concurrency() { return 0; }
            bot_acquire_run_slot() { echo "SINGLE [$EXTRA_PROMPT]"; exit 0; }
        """,
        "load-config.sh": """
            BOT_CONCURRENT_JOBS=3
            BOT_MAX_CONCURRENT_RUNS=3
            BOT_PROFILE=stub
            BOT_PROFILE_WORKTREES=false
            BOT_SSH_KEY_PATH=""
            BOT_GH_ACCOUNT=""
            bot_profile_mismatch() { return 1; }
        """,
        "parallel-jobs.sh": """
            bot_validate_concurrent_jobs() { return 0; }
            bot_run_parallel_jobs() { echo "PARALLEL"; exit 0; }
        """,
        "git-identity.sh": "bot_export_identity_env() { return 0; }",
        "review-gate.sh": "bot_validate_review_config() { return 0; }",
        "repo-hooks.sh": "",
        "terminal-title.sh": "",
    }

    @classmethod
    def run_sh(cls, tmp_path, *args, env=None):
        lib = tmp_path / "scripts" / "lib"
        lib.mkdir(parents=True)
        for name, body in cls.STUBS.items():
            (lib / name).write_text(textwrap.dedent(body))
        shutil.copy(RUN_SH, tmp_path / "run.sh")
        return subprocess.run(
            ["bash", str(tmp_path / "run.sh"), *args],
            capture_output=True,
            text=True,
            timeout=60,
            env={
                **{k: v for k, v in os.environ.items() if k != "BOT_RUN_JOB"},
                **(env or {}),
            },
        )

    @pytest.mark.parametrize(
        "args",
        [
            ("5", URL),
            (URL,),
            ("https://github.com/o/r/pull/9", "5"),
            ("--agent", "codex", URL),
            ("5", "tui", URL),
        ],
    )
    def test_a_url_runs_one_job_that_works_the_named_story(self, tmp_path, args):
        out = self.run_sh(tmp_path, *args)
        url = next(a for a in args if a.startswith("https://"))
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == f"SINGLE [{url}]"
        assert "running one job" in out.stderr

    def test_the_url_says_why_it_is_one_job(self, tmp_path):
        out = self.run_sh(tmp_path, self.URL)
        assert "names a story by URL: running one job" in out.stderr

    @pytest.mark.parametrize("args", [("5",), ("5", "some", "words"), ()])
    def test_without_a_url_the_jobs_are_started(self, tmp_path, args):
        out = self.run_sh(tmp_path, *args)
        assert out.stdout.strip() == "PARALLEL"

    def test_a_url_that_is_a_flags_value_names_no_story(self, tmp_path):
        out = self.run_sh(tmp_path, "--agent-bin", "https://example.com/bin")
        assert out.stdout.strip() == "PARALLEL"

    def test_a_job_started_by_the_supervisor_still_works_the_named_story(
        self, tmp_path
    ):
        out = self.run_sh(tmp_path, self.URL, env={"BOT_RUN_JOB": "2"})
        assert out.stdout.strip() == f"SINGLE [{self.URL}]"


class TestConfig:
    @staticmethod
    def load(tmp_path, bot):
        """BOT_CONCURRENT_JOBS as the real loader resolves it, in a copy of
        scripts/ beside a config of its own."""
        shutil.copytree(
            os.path.join(ROOT, "scripts"),
            tmp_path / "scripts",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (tmp_path / "projects").mkdir()
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "project": {
                        "name": "p",
                        "org": "o",
                        "prRepository": "o/p",
                        "issueRepository": "o/p",
                        "defaultBranch": "main",
                        "targetRepoPath": "../p",
                        "profile": "default",
                    },
                    "bot": {"username": "b", **bot},
                    "bestPractices": {"docsDir": "../p/docs"},
                }
            )
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                f"source {tmp_path}/scripts/lib/load-config.sh; "
                "bash -c 'echo \"jobs=$BOT_CONCURRENT_JOBS\"'",
            ],
            capture_output=True,
            text=True,
        )

    def test_the_default_is_one_job(self, tmp_path):
        out = self.load(tmp_path, {})
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "jobs=1"

    def test_the_configured_count_is_exported(self, tmp_path):
        out = self.load(tmp_path, {"concurrentJobs": 3, "maxConcurrentRuns": 3})
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "jobs=3"
