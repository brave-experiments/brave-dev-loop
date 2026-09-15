"""Tests for comparison-run mode: the flags, the session finder, the guards.

A comparison run redoes a story with a second tool and files issues for what the
first one did worse. Two things must hold whatever else changes: it is off unless
asked for, and it cannot reach anything public.
"""

import json
import os
import subprocess

import pytest

ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
GUARD_DIR = os.path.join(SCRIPTS_DIR, "comparison-guard")
RUN_SH = os.path.join(ROOT_DIR, "run.sh")
FIND_SESSION = os.path.join(SCRIPTS_DIR, "find-agent-session.py")

NEW_SHELL_SCRIPTS = [
    os.path.join(SCRIPTS_DIR, "comparison-run.sh"),
    os.path.join(SCRIPTS_DIR, "comparison-evaluate.sh"),
    os.path.join(SCRIPTS_DIR, "lib", "agent-launch.sh"),
    os.path.join(GUARD_DIR, "guard.sh"),
    os.path.join(GUARD_DIR, "gh"),
    os.path.join(GUARD_DIR, "git"),
]

VALUE_FLAGS = [
    "--comparison-agent",
    "--comparison-agent-bin",
    "--comparison-model",
    "--comparison-branch",
]


def run_sh_body():
    with open(RUN_SH) as f:
        return f.read()


# ── The flags ────────────────────────────────────────────────────────────────


class TestFlags:
    """The refusal paths run before run.sh sources any config or takes a slot,
    so they can be executed for real rather than asserted on as text."""

    @staticmethod
    def _run(*args):
        return subprocess.run(
            ["bash", RUN_SH, *args], capture_output=True, text=True, cwd=ROOT_DIR
        )

    @pytest.mark.parametrize("flag", VALUE_FLAGS)
    def test_a_value_flag_with_no_value_is_refused(self, flag):
        result = self._run("--comparison-run", flag)
        assert result.returncode == 1
        assert f"{flag} requires a value" in result.stderr

    @pytest.mark.parametrize("flag", VALUE_FLAGS)
    def test_configuring_a_comparison_without_asking_for_one_is_refused(self, flag):
        """Otherwise the run does the work once and silently compares nothing —
        the one outcome nobody wants from typing these flags."""
        result = self._run(f"{flag}=x")
        assert result.returncode == 1
        assert "--comparison-run was not given" in result.stderr

    @pytest.mark.parametrize("flag", ["--comparison-run", *VALUE_FLAGS])
    def test_the_flag_is_parsed_wherever_it_appears(self, flag):
        """The parsing chain runs before `tui` is consumed, so a comparison flag
        after `tui` must not be swallowed into the extra prompt."""
        body = run_sh_body()
        assert f'[[ "$arg" == "{flag}" ]]' in body, flag
        if flag != "--comparison-run":
            assert f'[[ "$arg" == {flag}=* ]]' in body, flag

    def test_the_comparison_agent_is_validated_like_the_base_agent(self):
        body = run_sh_body()
        assert body.count("claude|codex|cursor|bravebot) ;;") == 2
        assert "unsupported comparison agent" in body

    def test_the_comparison_agent_defaults_to_claude(self):
        """A second opinion from the same tool is not a second opinion."""
        body = run_sh_body()
        assert 'COMPARISON_AGENT="claude"' in body


# ── Where the three steps sit in the iteration ───────────────────────────────


class TestOrchestration:
    @staticmethod
    def _at(needle):
        body = run_sh_body()
        assert needle in body, needle
        return body.index(needle)

    def test_the_whole_block_is_gated_on_the_flag(self):
        body = run_sh_body()
        block = body.index(
            'if [ "$COMPARISON_RUN" = true ]; then\n    bot_slot_heartbeat'
        )
        for call in ("scripts/comparison-run.sh", "scripts/comparison-evaluate.sh"):
            assert body.index(call, block) > block, call

    def test_the_comparison_runs_after_the_base_run(self):
        assert self._at("scripts/comparison-run.sh") > self._at("stop_title_watch\n")

    def test_the_comparison_runs_before_the_completion_check(self):
        """The last iteration is the one most worth evaluating, and the
        completion check exits the loop."""
        assert self._at("scripts/comparison-run.sh") < self._at("COMPLETION_CHECK=0")

    def test_the_evaluator_only_runs_with_something_to_compare(self):
        body = run_sh_body()
        start = body.index("scripts/comparison-run.sh")
        between = body[start : body.index("scripts/comparison-evaluate.sh", start)]
        assert 'if [ "$COMPARISON_RC" -ne 0 ]' in between

    def test_neither_step_can_kill_the_loop(self):
        """run.sh is `set -e`: a comparison that fails is a missing second
        opinion, not a failed story."""
        body = run_sh_body()
        assert "|| COMPARISON_RC=$?" in body
        evaluate = body.index("scripts/comparison-evaluate.sh")
        assert "|| echo" in body[evaluate : evaluate + 900]

    def test_the_base_session_is_dated_before_the_base_launch(self):
        """bravebot cannot be handed a session id, so its session is found by
        what appeared after this timestamp."""
        assert self._at("BASE_STARTED_AT=$(date +%s)") < self._at(
            "# Run the agent from the bot directory"
        )

    def test_the_slot_stays_alive_across_two_more_agents(self):
        body = run_sh_body()
        block = body[body.index("Comparison run (step 2 of 3)") :]
        assert (
            block[: block.index("COMPLETION_CHECK=0")].count("bot_slot_heartbeat") >= 2
        )

    def test_stdout_says_which_step_is_running(self):
        body = run_sh_body()
        assert "Comparison run (step 2 of 3)" in body
        assert "Evaluator run (step 3 of 3)" in body


# ── find-agent-session.py ────────────────────────────────────────────────────


def write_bravebot_session(home, cwd, session_id, updated, audit=False):
    slug = "".join(c if c.isalnum() else "-" for c in cwd)
    directory = os.path.join(home, ".bravebot", "sessions", slug)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{session_id}.json")
    with open(path, "w") as f:
        json.dump(
            {
                "id": session_id,
                "directory": cwd,
                "started": updated,
                "updated": updated,
                "conversation": {"messages": []},
            },
            f,
        )
    if audit:
        with open(os.path.join(directory, f"{session_id}.audit.jsonl"), "w") as f:
            f.write('{"gate":"write","decision":"allow"}\n')
    return path


def find_session(home, agent, cwd, **kwargs):
    args = [FIND_SESSION, "--agent", agent, "--cwd", cwd, "--home", home]
    for key, value in kwargs.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    return subprocess.run(["python3", *args], capture_output=True, text=True)


class TestFindSession:
    def test_the_newest_session_for_the_directory_wins(self, tmp_dir):
        cwd = os.path.join(tmp_dir, "bot")
        os.makedirs(cwd)
        write_bravebot_session(tmp_dir, cwd, "1000-1", 1000)
        write_bravebot_session(tmp_dir, cwd, "2000-2", 2000)
        result = find_session(tmp_dir, "bravebot", cwd)
        assert result.returncode == 0, result.stderr
        found = json.loads(result.stdout)
        assert found["sessionId"] == "2000-2"
        assert found["resumeCommand"] == "bravebot --resume 2000-2"

    def test_a_session_from_another_directory_is_not_this_run_s(self, tmp_dir):
        """The record's own `directory` field decides, not the slug: bravebot
        renaming its directories must not silently return a stranger's session."""
        cwd = os.path.join(tmp_dir, "bot")
        other = os.path.join(tmp_dir, "elsewhere")
        os.makedirs(cwd)
        os.makedirs(other)
        write_bravebot_session(tmp_dir, other, "3000-3", 3000)
        assert find_session(tmp_dir, "bravebot", cwd).returncode == 1

    def test_sessions_older_than_the_launch_are_ignored(self, tmp_dir):
        cwd = os.path.join(tmp_dir, "bot")
        os.makedirs(cwd)
        write_bravebot_session(tmp_dir, cwd, "1000-1", 1000)
        assert find_session(tmp_dir, "bravebot", cwd, since=2000).returncode == 1
        assert find_session(tmp_dir, "bravebot", cwd, since=500).returncode == 0

    def test_the_base_session_is_excluded_from_the_comparison_s(self, tmp_dir):
        """Both runs can be the same tool in the same directory, and then the
        newest session is the base run's until it is ruled out."""
        cwd = os.path.join(tmp_dir, "bot")
        os.makedirs(cwd)
        write_bravebot_session(tmp_dir, cwd, "1000-base", 1000)
        write_bravebot_session(tmp_dir, cwd, "2000-comparison", 2000)
        result = find_session(tmp_dir, "bravebot", cwd, exclude="2000-comparison")
        assert json.loads(result.stdout)["sessionId"] == "1000-base"

    def test_the_audit_log_comes_along_when_there_is_one(self, tmp_dir):
        """For a TUI base run the transcript and the audit log are the only
        record there is — run.sh writes no iteration log then."""
        cwd = os.path.join(tmp_dir, "bot")
        os.makedirs(cwd)
        write_bravebot_session(tmp_dir, cwd, "1000-1", 1000, audit=True)
        found = json.loads(find_session(tmp_dir, "bravebot", cwd).stdout)
        assert found["auditLog"].endswith("1000-1.audit.jsonl")
        assert os.path.exists(found["auditLog"])

    def test_a_session_with_no_audit_log_reports_none(self, tmp_dir):
        cwd = os.path.join(tmp_dir, "bot")
        os.makedirs(cwd)
        write_bravebot_session(tmp_dir, cwd, "1000-1", 1000)
        assert (
            json.loads(find_session(tmp_dir, "bravebot", cwd).stdout)["auditLog"] == ""
        )

    def test_claude_resolves_the_transcript_for_a_known_id(self, tmp_dir):
        cwd = os.path.join(tmp_dir, "bot")
        os.makedirs(cwd)
        slug = "".join(c if c.isalnum() else "-" for c in os.path.abspath(cwd))
        projects = os.path.join(tmp_dir, ".claude", "projects", slug)
        os.makedirs(projects)
        transcript = os.path.join(projects, "abc-123.jsonl")
        with open(transcript, "w") as f:
            f.write("{}\n")
        found = json.loads(
            find_session(tmp_dir, "claude", cwd, session_id="abc-123").stdout
        )
        assert found["transcript"] == transcript
        assert found["resumeCommand"] == "claude --resume abc-123"

    def test_cursor_says_it_keeps_no_session_to_read(self, tmp_dir):
        result = find_session(tmp_dir, "cursor", tmp_dir)
        assert result.returncode == 2
        assert "iteration log" in result.stderr


# ── The guards ───────────────────────────────────────────────────────────────


@pytest.fixture
def guarded_path(tmp_dir):
    """The shim directory in front of a fake `gh` and `git` that just report
    being reached, so a pass-through is distinguishable from a refusal."""
    fake = os.path.join(tmp_dir, "bin")
    os.makedirs(fake)
    for tool in ("gh", "git"):
        path = os.path.join(fake, tool)
        with open(path, "w") as f:
            f.write(f'#!/bin/bash\necho "real {tool} $*"\n')
        os.chmod(path, 0o755)
    return f"{GUARD_DIR}:{fake}:/usr/bin:/bin"


def guarded(command, guarded_path, **env):
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        env={"PATH": guarded_path, **env},
    )


class TestGuards:
    @pytest.mark.parametrize(
        "command",
        [
            "gh pr create --title x",
            "gh pr edit 1 --body x",
            "gh pr merge 1",
            "gh pr comment 1 --body x",
            "gh pr review 1 --approve",
            "gh issue create --title x",
            "gh issue comment 1 --body x",
            "gh issue close 1",
            "gh release create v1",
            "gh repo fork",
            "gh workflow run ci",
            "gh secret set TOKEN",
            "git push origin HEAD",
        ],
    )
    def test_reaching_anyone_is_refused(self, command, guarded_path):
        result = guarded(command, guarded_path)
        assert result.returncode == 1, result.stdout
        assert "comparison-run guard: refused" in result.stderr
        assert "real" not in result.stdout

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr view 1",
            "gh pr diff 1",
            "gh pr list",
            "gh issue list",
            "gh issue view 1",
            "gh api repos/a/b",
            "gh api repos/a/b -X GET",
            "gh api repos/a/b --method=GET",
            "git status",
            "git commit -m x",
            "git worktree list",
        ],
    )
    def test_reading_and_committing_pass_through(self, command, guarded_path):
        """The commit in the comparison worktree is what the evaluator reads, so
        everything local has to keep working."""
        result = guarded(command, guarded_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("real ")

    @pytest.mark.parametrize(
        "command",
        [
            "gh api repos/a/b -X POST",
            "gh api repos/a/b --method PATCH",
            "gh api repos/a/b --method=DELETE",
            "gh api repos/a/b/issues -f title=x",
            "gh api repos/a/b/issues --field title=x",
            "gh api repos/a/b/issues --input body.json",
        ],
    )
    def test_gh_api_is_a_read_only_while_it_stays_a_get(self, command, guarded_path):
        result = guarded(command, guarded_path)
        assert result.returncode == 1, result.stdout
        assert "comparison-run guard: refused" in result.stderr

    def test_the_evaluator_may_file_issues_and_nothing_more(self, guarded_path):
        allow = {"BOT_COMPARISON_GUARD_ALLOW": "issue-create"}
        assert (
            guarded("gh issue create --title x", guarded_path, **allow).returncode == 0
        )
        for still_refused in (
            "gh pr create",
            "gh issue comment 1 --body x",
            "git push",
        ):
            result = guarded(still_refused, guarded_path, **allow)
            assert result.returncode == 1, still_refused

    def test_the_refusal_says_where_the_rule_is_written_down(self, guarded_path):
        result = guarded("gh pr create", guarded_path)
        assert "docs/comparison-runs.md" in result.stderr

    def test_the_shim_does_not_find_itself(self, guarded_path):
        """The shim is first on PATH; resolving the real binary with
        `command -v` would re-run the shim until the stack ran out."""
        result = guarded("gh pr list", guarded_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "real gh pr list"


class TestLoopScriptsRefuseAComparisonRun:
    def test_the_prd_is_not_the_comparison_run_s_to_move(self, tmp_dir):
        """Moving the story on would make the base run's own status update
        illegal, and the PRD would then describe work nobody pushed."""
        result = subprocess.run(
            ["python3", os.path.join(SCRIPTS_DIR, "update-prd-status.py")],
            capture_output=True,
            text=True,
            env={**os.environ, "BOT_COMPARISON_RUN": "1"},
        )
        assert result.returncode == 1
        assert "comparison run" in result.stderr

    def test_the_progress_log_is_not_the_comparison_run_s_to_append_to(self, tmp_dir):
        progress = os.path.join(tmp_dir, "progress.txt")
        result = subprocess.run(
            [
                os.path.join(SCRIPTS_DIR, "append-progress.sh"),
                "--progress-file",
                progress,
            ],
            input="## note\n",
            capture_output=True,
            text=True,
            env={**os.environ, "BOT_COMPARISON_RUN": "1"},
        )
        assert result.returncode == 1
        assert "comparison run" in result.stderr
        assert not os.path.exists(progress)


class TestNewScripts:
    @pytest.mark.parametrize("path", NEW_SHELL_SCRIPTS)
    def test_syntax(self, path):
        assert subprocess.run(["bash", "-n", path]).returncode == 0

    @pytest.mark.parametrize(
        "path",
        [
            p
            for p in NEW_SHELL_SCRIPTS
            if not p.endswith(("guard.sh", "agent-launch.sh"))
        ]
        + [FIND_SESSION],
    )
    def test_executable(self, path):
        assert os.access(path, os.X_OK), path

    @pytest.mark.parametrize(
        "path", [os.path.join(SCRIPTS_DIR, "comparison-evaluate.sh")]
    )
    def test_the_evaluator_reads_its_rules_from_the_doc(self, path):
        """Rules in a shell string cannot be reviewed or diffed sensibly."""
        with open(path) as f:
            assert "docs/comparison-evaluation.md" in f.read()
        assert os.path.exists(
            os.path.join(ROOT_DIR, "docs", "comparison-evaluation.md")
        )
