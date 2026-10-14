"""A signal ends run.sh, after handing back its claim and slot.

The trap lines are read out of run.sh and run in a loop that would carry on
for several seconds, so the test fails if run.sh's own lines let it.
"""

import os
import signal
import subprocess
import time

import pytest

RUN_SH = os.path.join(os.path.dirname(__file__), os.pardir, "run.sh")


def trap_lines():
    with open(RUN_SH) as f:
        return [
            line
            for line in f.read().splitlines()
            if line.startswith("trap ") and ("cleanup_run" in line or "'exit " in line)
        ]


@pytest.mark.parametrize(
    "sig,status",
    [(signal.SIGINT, 130), (signal.SIGTERM, 143), (signal.SIGHUP, 129)],
)
def test_a_signal_runs_the_cleanup_once_and_ends_the_run(tmp_path, sig, status):
    out = tmp_path / "out"
    ready = tmp_path / "ready"
    script = "\n".join(
        [
            f'cleanup_run() {{ echo cleanup >> "{out}"; }}',
            *trap_lines(),
            f'touch "{ready}"',
            "for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 0.3; done",
            f'echo carried-on >> "{out}"',
        ]
    )
    # A suite started as a background job inherits SIGINT ignored, and a shell
    # cannot trap a signal that was ignored when it started.
    proc = subprocess.Popen(
        ["bash", "-c", script],
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("the script never installed its traps")
    proc.send_signal(sig)
    assert proc.wait(timeout=30) == status
    assert out.read_text().split() == ["cleanup"]


def test_the_run_still_cleans_up_when_it_ends_by_itself(tmp_path):
    out = tmp_path / "out"
    script = "\n".join(
        [f'cleanup_run() {{ echo cleanup >> "{out}"; }}', *trap_lines(), "exit 0"]
    )
    assert subprocess.run(["bash", "-c", script]).returncode == 0
    assert out.read_text().split() == ["cleanup"]
