"""run.sh's bravebot launch: the flags the loop starts bravebot with.

run.sh is not run whole -- it claims a story and talks to GitHub -- so the
bravebot branch is cut out of it and run against a fake binary in tmp_path
that records its argv.
"""

import os
import subprocess

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
RUN_SH = os.path.join(ROOT_DIR, "run.sh")

PROMPT = "work on US-1"

BYPASS = "--dangerously-bypass-approvals-and-sandbox"

# One argument per NUL: the prompt in a real run spans lines.
FAKE_BRAVEBOT = """#!/bin/bash
printf '%s\\0' "$@" > "$FAKE_ARGV"
echo done
"""


def bravebot_branch():
    """The bravebot arm of run.sh's agent chain, as a statement of its own."""
    with open(RUN_SH, encoding="utf-8") as fh:
        body = fh.read()
    # The arm that launches, not the earlier ones that only pick its model and binary.
    head = '  elif [ "$BOT_AGENT" = "bravebot" ]; then\n    BRAVEBOT_MODEL_FLAG=""\n'
    start = body.index(head) + len('  elif [ "$BOT_AGENT" = "bravebot" ]; then\n')
    return (
        'if [ "$BOT_AGENT" = "bravebot" ]; then\n'
        + body[start : body.index('  elif [ "$BOT_AGENT" = "cursor" ]', start)]
        + "fi\n"
    )


def launched_with(tmp_path, tui, model=""):
    fake = tmp_path / "bravebot"
    fake.write_text(FAKE_BRAVEBOT)
    fake.chmod(0o755)
    argv = tmp_path / "argv"
    env = dict(
        os.environ,
        SCRIPT_DIR=ROOT_DIR,
        BOT_AGENT="bravebot",
        BOT_BRAVEBOT_BIN=str(fake),
        BOT_BRAVEBOT_MODEL=model,
        USE_TUI="true" if tui else "false",
        AGENT_PROMPT=PROMPT,
        ITERATION_SECONDS="60",
        ITERATION_LOG=str(tmp_path / "iteration.log"),
        FAKE_ARGV=str(argv),
    )
    subprocess.run(
        ["bash", "-c", bravebot_branch()],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    return argv.read_text().split("\0")[:-1]


@pytest.mark.parametrize("tui", [False, True], ids=["one-shot", "tui"])
class TestBravebotLaunch:
    def test_it_bypasses_permissions_and_the_sandbox(self, tmp_path, tui):
        assert BYPASS in launched_with(tmp_path, tui)

    def test_it_does_not_also_pass_the_flag_that_leaves_the_sandbox_on(
        self, tmp_path, tui
    ):
        assert "--dangerously-skip-permissions" not in launched_with(tmp_path, tui)

    def test_the_prompt_is_still_passed(self, tmp_path, tui):
        args = launched_with(tmp_path, tui)
        assert PROMPT in args
        if tui:
            assert args[args.index("-i") + 1] == PROMPT

    def test_a_model_is_still_passed(self, tmp_path, tui):
        args = launched_with(tmp_path, tui, model="sonnet")
        assert args[args.index("--model") + 1] == "sonnet"
