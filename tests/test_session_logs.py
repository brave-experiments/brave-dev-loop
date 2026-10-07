"""run.sh keeps each run's logs in a session directory, and the housekeeping
around the agent out of the terminal."""

import os
import re
import subprocess

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_SH = os.path.join(ROOT_DIR, "run.sh")


def run_sh():
    with open(RUN_SH) as f:
        return f.read()


def quiet_step(tmp_path, *step):
    """Run the quiet_step function from run.sh around `step`."""
    body = run_sh()
    function = re.search(r"^quiet_step\(\) \{\n.*?^\}\n", body, re.M | re.S).group(0)
    log = tmp_path / "housekeeping.log"
    script = (
        f'{function}\nHOUSEKEEPING_LOG="{log}"\nquiet_step "my step" {" ".join(step)}\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
    )
    return result, log.read_text()


class TestSessionLogs:
    def test_every_log_is_in_the_session_directory(self):
        body = run_sh()
        assert (
            'ITERATION_LOG="$RUN_SESSION_DIR/iteration-loop-${loop_count}.log"' in body
        )
        assert 'COMPARISON_LOG="$RUN_SESSION_DIR/comparison-loop-' in body
        assert 'EVALUATOR_LOG="$RUN_SESSION_DIR/evaluator-loop-' in body
        assert "LOGS_DIR" not in body

    def test_the_session_directory_is_outside_the_checkout(self):
        body = run_sh()
        assert (
            'RUN_SESSION_DIR="${BRAVE_DEV_LOOP_HOME:-$HOME/.brave-dev-loop}/sessions/$RUN_SESSION_ID"'
            in body
        )
        assert (
            'RUN_SESSION_ID="$(date -u +%Y%m%dT%H%M%SZ)-slot-${BOT_RUN_SLOT}"' in body
        )

    def test_no_housekeeping_step_writes_to_the_terminal_directly(self):
        body = run_sh()
        for script in (
            "scripts/reset-run-state.sh",
            "scripts/sync-prd.sh",
            "scripts/sweep-in-progress.py",
            'scripts/add-backlog-to-prd.py" --named',
            'scripts/clean-worktrees.py" --max-age-hours',
            'scripts/clean-worktrees.py"',
        ):
            at = body.index(script)
            assert "quiet_step" in body[max(0, at - 200) : at], script

    def test_output_goes_to_the_log_and_not_the_terminal(self, tmp_path):
        result, log = quiet_step(tmp_path, "echo", "Retired 0 merged PR\\(s\\)")
        assert result.returncode == 0
        assert result.stdout == "" and result.stderr == ""
        assert "my step (exit 0)" in log
        assert "Retired 0 merged PR(s)" in log

    def test_a_warning_reaches_the_terminal_and_the_log(self, tmp_path):
        result, log = quiet_step(
            tmp_path, "bash", "-c", "'echo noise; echo WARNING: sync failed'"
        )
        assert result.returncode == 0
        assert result.stderr.strip() == "WARNING: sync failed"
        assert "noise" in log and "WARNING: sync failed" in log

    def test_a_failed_step_returns_its_status_and_names_the_log(self, tmp_path):
        result, log = quiet_step(tmp_path, "bash", "-c", "'echo boom; exit 3'")
        assert result.returncode == 3
        assert "boom" in result.stderr
        assert "my step failed (exit 3)" in result.stderr
        assert str(tmp_path / "housekeeping.log") in result.stderr
        assert "my step (exit 3)" in log
