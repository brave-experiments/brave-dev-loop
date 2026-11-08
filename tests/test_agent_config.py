"""The agents the schedules and the review sessions run, from config.json.

bot.agent decides the flags on every run.sh cron line, resolved when the schedules
are installed; reviewer.agent decides what review-agent.sh launches. Both default to
claude, and a claude deployment's crontab must not change by a byte.
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


def test_the_run_agent_does_not_touch_the_review_group(tmp_dir):
    """bot.agent is for run.sh. A review-only install must not need its binary."""
    result = render(tmp_dir, bot={"agent": "bravebot"}, group="review")
    assert result.returncode == 0, result.stderr
    assert "--agent" not in result.stdout


def test_a_review_agent_is_on_the_path_but_not_on_any_line(tmp_dir):
    """review-session.sh reads reviewer.agent when the job fires, so the lines do
    not change; only the block's PATH needs the binary's directory."""
    binary = fake_agent(tmp_dir)
    claude_only = render(tmp_dir, group="review").stdout
    out = render(
        tmp_dir,
        bot={"bravebotBin": binary},
        reviewer={"agent": "bravebot"},
        group="review",
    ).stdout
    assert os.path.dirname(binary) in out
    assert [ln for ln in out.splitlines() if ln[:1].isdigit()] == [
        ln for ln in claude_only.splitlines() if ln[:1].isdigit()
    ]


def review_cmd(tmp_dir, agent, kind="review", extra_env=None):
    """BOT_REVIEW_AGENT_CMD, one word to a line, as review-agent.sh builds it."""
    root = Schedules._bot_dir(tmp_dir, "default")
    script = (
        f'source "{root}/scripts/lib/load-config.sh"\n'
        f'source "{root}/scripts/lib/review-agent.sh"\n'
        f'bot_review_agent_cmd {kind} "/review-prs --work-dir W" sonnet \'{{"permissions":{{"deny":[]}}}}\' '
        f'"{tmp_dir}/work" "{tmp_dir}/target"\n'
        'printf "%s\\n" "${BOT_REVIEW_AGENT_CMD[@]}"\n'
        'echo "TMP=$BOT_REVIEW_AGENT_TMP"\n'
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": tmp_dir,
        "BOT_REVIEW_AGENT": agent,
        "BOT_BRAVEBOT_BIN": "/opt/bravebot",
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env
    )


def test_claude_reviews_exactly_as_before(tmp_dir):
    out = review_cmd(tmp_dir, "claude").stdout.splitlines()
    assert out[0].endswith("claude")
    assert out[1:4] == ["-p", "/review-prs --work-dir W", "--model"]
    assert "--permission-mode" in out and "dontAsk" in out
    assert out.count("--add-dir") == 2
    assert out[-2] == "--strict-mcp-config"


def test_bravebot_reviews_without_a_fetch_tool_and_with_the_network_closed(tmp_dir):
    out = review_cmd(tmp_dir, "bravebot").stdout.splitlines()
    assert out[0] == "/opt/bravebot"
    assert out[1] == "/review-prs --work-dir W"
    tools = out[out.index("--tools") + 1].split(",")
    assert "fetch_url" not in tools and "download_url" not in tools
    assert "run" in tools
    assert out[out.index("--run-network") + 1] == "closed"
    # Writes are held to the work directory; the bot directory is refused.
    assert out[out.index("--sandbox-allow-write") + 1].endswith("/work")
    assert "--sandbox-deny-write" in out


def test_a_bravebot_reply_session_gets_no_program_and_no_delegate(tmp_dir):
    out = review_cmd(tmp_dir, "bravebot", kind="reply").stdout.splitlines()
    tools = out[out.index("--tools") + 1].split(",")
    assert "run" not in tools and "spawn_agent" not in tools


def test_bravebot_settings_deny_writes_outside_the_work_directory(tmp_dir):
    """Bypass mode ignores the Edit allow rule, so the confinement is a deny."""
    out = review_cmd(tmp_dir, "bravebot").stdout.splitlines()
    settings = out[out.index("--settings") + 1]
    with open(settings) as f:
        deny = json.load(f)["permissions"]["deny"]
    assert any(rule.startswith("Edit(//") and "/bot/" in rule for rule in deny)
    assert any(rule.endswith("/target/**)") for rule in deny)
    assert not any(rule.endswith("/work/**)") for rule in deny)


def test_an_agent_that_cannot_review_is_refused(tmp_dir):
    result = review_cmd(tmp_dir, "codex")
    assert "cannot review" in result.stderr
