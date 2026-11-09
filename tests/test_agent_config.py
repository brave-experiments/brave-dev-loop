"""The agent the schedules run, from config.json.

bot.agent decides the flags on every run.sh cron line, resolved when the schedules
are installed. It defaults to claude, and a claude deployment's crontab must not
change by a byte.
"""

import json
import os
import subprocess

import pytest
import test_scripts

Schedules = test_scripts.TestProjectSchedules

SCRIPTS = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")


def fake_agent(tmp_dir, name="bravebot"):
    """An executable in a directory that is on no PATH, as nvm's is for cron."""
    directory = os.path.join(tmp_dir, "nvm", "bin")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(path, 0o755)
    return path


def render(tmp_dir, profile="bravebot", bot=None, reviewer=None, group=None):
    root = Schedules._bot_dir(tmp_dir, profile)
    path = os.path.join(root, "config.json")
    with open(path) as f:
        config = json.load(f)
    config["bot"].update(bot or {})
    config["reviewer"] = reviewer or {}
    with open(path, "w") as f:
        json.dump(config, f)
    args = [os.path.join(root, "scripts", "sync-schedules.sh"), "--print"]
    if group:
        args += ["--group", group]
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        # No bravebot on this PATH: the path has to come from config.
        env={"PATH": "/usr/bin:/bin", "HOME": tmp_dir},
    )
    return result


def run_jobs(block):
    return [j for j in Schedules._jobs(block) if "./run.sh " in j]


@pytest.mark.parametrize(
    "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
)
def test_claude_is_the_default_and_adds_no_flag(tmp_dir, profile):
    result = render(tmp_dir, profile)
    assert result.returncode == 0, result.stderr
    jobs = run_jobs(result.stdout)
    assert jobs
    for job in jobs:
        assert "--agent" not in job, job
    assert "nvm" not in result.stdout


@pytest.mark.parametrize(
    "profile", ["brave-core", "bravebot", "default", "brave-dev-loop"]
)
def test_every_run_job_carries_the_configured_agent_and_its_absolute_path(
    tmp_dir, profile
):
    binary = fake_agent(tmp_dir)
    result = render(tmp_dir, profile, bot={"agent": "bravebot", "bravebotBin": binary})
    assert result.returncode == 0, result.stderr
    jobs = run_jobs(result.stdout)
    assert jobs
    for job in jobs:
        assert f" --agent bravebot --agent-bin {binary} ;" in job, job


def test_the_agents_directory_is_put_on_the_blocks_path(tmp_dir):
    """The binary is a `#!/usr/bin/env node` script; node lives beside it."""
    binary = fake_agent(tmp_dir)
    out = render(
        tmp_dir, bot={"agent": "bravebot", "bravebotBin": binary}, group="run"
    ).stdout
    (path_line,) = [ln for ln in out.splitlines() if ln.startswith("PATH=")]
    assert path_line.split("=", 1)[1].split(":")[1] == os.path.dirname(binary)


def test_an_agent_that_cannot_be_found_stops_the_install(tmp_dir):
    result = render(tmp_dir, bot={"agent": "bravebot"})
    assert result.returncode != 0
    assert "bravebot binary is not on PATH" in result.stderr


def test_an_unknown_agent_is_refused(tmp_dir):
    result = render(tmp_dir, bot={"agent": "nope"})
    assert result.returncode != 0
    assert "unsupported agent 'nope'" in result.stderr
