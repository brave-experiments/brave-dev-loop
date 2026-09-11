"""Tests for the terminal tab title: lib/terminal-title.sh and watch-pr-title.sh."""

import json
import os
import subprocess
import time

SCRIPTS = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")
LIB = os.path.join(SCRIPTS, "lib")
WATCH_SCRIPT = os.path.join(SCRIPTS, "watch-pr-title.sh")

OSC_START = "\033]0;"
OSC_END = "\007"


def title(issue, pr, story_id, story_title):
    """The tab title text the run would set for these story fields."""
    script = f'source {LIB}/terminal-title.sh\nbot_story_title "$1" "$2" "$3" "$4"\n'
    proc = subprocess.run(
        ["bash", "-c", script, "bash", issue, pr, story_id, story_title],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def set_title(text, tty_path):
    script = f'source {LIB}/terminal-title.sh\nbot_set_terminal_title "$1"\n'
    return subprocess.run(
        ["bash", "-c", script, "bash", text],
        capture_output=True,
        text=True,
        env={**os.environ, "BOT_TITLE_TTY": tty_path},
    )


def write_prd(path, pr_number=None):
    prd = {
        "stories": [
            {"id": "US-036", "title": "trust map", "prNumber": pr_number},
            {"id": "US-999", "title": "other story", "prNumber": 111},
        ]
    }
    with open(path, "w") as f:
        json.dump(prd, f)


def wait_for(predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


# ── Title text ───────────────────────────────────────────────────────────────


def test_issue_and_pr_lead_the_title():
    assert title("29", "250", "US-036", "vouch prompt bytes") == (
        "#29 PR #250 vouch prompt bytes"
    )


def test_pr_omitted_until_there_is_one():
    assert title("29", "", "US-036", "vouch prompt bytes") == "#29 vouch prompt bytes"


def test_pr_alone_when_the_story_names_no_issue():
    assert title("", "250", "US-031", "drops a claim") == "PR #250 drops a claim"


def test_story_id_stands_in_when_there_is_no_number():
    assert title("", "", "US-031", "drops a claim") == "US-031 drops a claim"


def test_control_characters_in_a_story_title_are_stripped():
    # An issue title is text the bot did not write: a BEL would end the escape
    # sequence early and leave "malicious" to be read as terminal commands.
    assert title("29", "", "US-036", "bytes\007malicious\033]0;spoofed") == (
        "#29 bytesmalicious]0;spoofed"
    )


# ── Writing to the terminal ──────────────────────────────────────────────────


def test_set_title_writes_an_osc_sequence(tmp_path):
    tty = tmp_path / "tty"
    tty.write_text("")
    proc = set_title("#29 PR #250 vouch prompt bytes", str(tty))
    assert proc.returncode == 0, proc.stderr
    assert tty.read_text() == f"{OSC_START}#29 PR #250 vouch prompt bytes{OSC_END}"
    assert proc.stdout == ""


def test_set_title_is_a_no_op_without_a_terminal(tmp_path):
    # A run started by a scheduler has no controlling terminal.
    proc = set_title("#29 vouch prompt bytes", str(tmp_path / "nope" / "tty"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


# ── The PR watcher ───────────────────────────────────────────────────────────


def test_watcher_retitles_when_the_pr_appears(tmp_path):
    prd = tmp_path / "prd.json"
    tty = tmp_path / "tty"
    tty.write_text("")
    write_prd(prd)

    proc = subprocess.Popen(
        ["bash", WATCH_SCRIPT, str(prd), "US-036", "29", "vouch prompt bytes", "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "BOT_TITLE_TTY": str(tty)},
    )
    try:
        time.sleep(2.5)
        assert tty.read_text() == "", "titled the tab before the PR existed"

        write_prd(prd, pr_number=250)
        assert wait_for(lambda: tty.read_text() != ""), "never saw the PR"
        assert tty.read_text() == (
            f"{OSC_START}#29 PR #250 vouch prompt bytes{OSC_END}"
        )
        # One title, then done: the watcher has nothing left to watch.
        assert wait_for(lambda: proc.poll() is not None), "watcher kept polling"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_watcher_gives_up_when_its_run_is_gone(tmp_path):
    # A run killed outright never stops the watcher, so the watcher checks.
    prd = tmp_path / "prd.json"
    tty = tmp_path / "tty"
    tty.write_text("")
    write_prd(prd)

    dead_run = subprocess.Popen(["sleep", "60"])
    proc = subprocess.Popen(
        ["bash", WATCH_SCRIPT, str(prd), "US-036", "29", "vouch prompt bytes", "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "BOT_TITLE_TTY": str(tty),
            "BOT_RUN_PID": str(dead_run.pid),
        },
    )
    try:
        dead_run.kill()
        dead_run.wait(timeout=10)
        assert wait_for(lambda: proc.poll() is not None), "kept polling for a dead run"
        write_prd(prd, pr_number=250)
        time.sleep(1.5)
        assert tty.read_text() == "", "retitled a tab it no longer owns"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_watcher_ignores_another_story_getting_a_pr(tmp_path):
    prd = tmp_path / "prd.json"
    tty = tmp_path / "tty"
    tty.write_text("")
    write_prd(prd)

    proc = subprocess.Popen(
        ["bash", WATCH_SCRIPT, str(prd), "US-036", "29", "vouch prompt bytes", "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "BOT_TITLE_TTY": str(tty)},
    )
    try:
        time.sleep(2.5)
        assert tty.read_text() == "", "took US-999's PR number"
    finally:
        proc.kill()
        proc.wait(timeout=10)
