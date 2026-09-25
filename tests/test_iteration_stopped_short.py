"""scripts/iteration-stopped-short.py: telling an iteration that stopped short.

git is real, in tmp_path: what the script reports of a story's work is git's
own view of the worktree that holds its branch.
"""

import json
import os
import re
import subprocess
import sys

import pytest

ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPT = os.path.join(ROOT_DIR, "scripts", "iteration-stopped-short.py")
RUN_SH = os.path.join(ROOT_DIR, "run.sh")
STORY = "US-313"
BRANCH = "fix-offer-the-tools"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def new_repo(path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    git(path, "config", "user.name", "testbot")
    git(path, "config", "user.email", "testbot@example.com")
    git(path, "config", "commit.gpgsign", "false")
    (path / "README").write_text("base\n")
    git(path, "add", "README")
    git(path, "commit", "-q", "-m", "base")


def write_prd(path, status, branch=BRANCH):
    story = {"id": STORY, "status": status}
    if branch:
        story["branchName"] = branch
    path.write_text(
        json.dumps({"stories": [{"id": "US-31", "status": "pending"}, story]})
    )


def check(
    tmp_path,
    start="pending",
    end="pending",
    progress="",
    offset=0,
    repo=None,
    branch=BRANCH,
    resumed=0,
):
    prd = tmp_path / "prd.json"
    write_prd(prd, end, branch)
    log = tmp_path / "progress.txt"
    log.write_text(progress)
    if repo is None:
        repo = tmp_path / "target"
        if not repo.exists():
            new_repo(repo)
    result = subprocess.run(
        [
            sys.executable,
            SCRIPT,
            "--prd",
            str(prd),
            "--story-id",
            STORY,
            "--start-status",
            start,
            "--start-branch",
            branch or "",
            "--progress-file",
            str(log),
            "--progress-offset",
            str(offset),
            "--repo",
            str(repo),
            "--resumed",
            str(resumed),
            "--session-id",
            "0f1e",
            "--log",
            "/logs/iteration.log",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def heading(story, status="pending"):
    return f"## 2026-09-25 10:00 - {story} - Status: {status} (iteration ended, no transition)\n"


class TestStoppedShort:
    def test_a_story_left_pending_with_nothing_recorded_stopped_short(self, tmp_path):
        assert check(tmp_path)["stoppedShort"] is True

    def test_an_entry_written_during_the_iteration_is_a_record(self, tmp_path):
        old = heading("US-200")
        found = check(tmp_path, progress=old + heading(STORY), offset=len(old))
        assert found["recorded"] is True
        assert found["stoppedShort"] is False

    def test_an_entry_for_another_story_whose_id_it_begins_is_not_a_record(
        self, tmp_path
    ):
        for other in ("US-31", "US-3130", "US-313-b"):
            assert check(tmp_path, progress=heading(other))["stoppedShort"] is True, (
                other
            )

    def test_an_entry_from_before_the_iteration_is_not_a_record(self, tmp_path):
        old = heading(STORY)
        assert check(tmp_path, progress=old, offset=len(old))["stoppedShort"] is True

    def test_a_mention_outside_a_heading_is_not_a_record(self, tmp_path):
        text = heading("US-200") + f"- Blocked on {STORY}\n"
        assert check(tmp_path, progress=text)["stoppedShort"] is True

    def test_a_log_shorter_than_the_offset_was_rotated_and_is_read_whole(
        self, tmp_path
    ):
        assert (
            check(tmp_path, progress=heading(STORY), offset=10_000)["recorded"] is True
        )

    @pytest.mark.parametrize("end", ["committed", "pushed", "skipped", "invalid"])
    def test_a_story_that_moved_did_not_stop_short(self, tmp_path, end):
        assert check(tmp_path, end=end)["stoppedShort"] is False

    @pytest.mark.parametrize("start", ["committed", "pushed", "skipped"])
    def test_only_a_pending_story_can_stop_short(self, tmp_path, start):
        assert check(tmp_path, start=start, end=start)["stoppedShort"] is False

    def test_the_entry_it_writes_is_itself_a_record(self, tmp_path):
        """What run.sh appends must stop a later check from writing another."""
        found = check(tmp_path, resumed=2)
        assert found["entry"].startswith("## ")
        assert (
            f" - {STORY} - Status: pending (iteration ended, no transition)"
            in found["entry"]
        )
        assert "resumed 2 time(s)" in found["entry"]
        assert "`claude -r 0f1e`" in found["entry"]
        assert found["entry"].rstrip().endswith("---")
        assert check(tmp_path, progress=found["entry"])["recorded"] is True


class TestWork:
    def test_it_counts_what_only_the_worktree_holds(self, tmp_path):
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        repo = tmp_path / "target"
        new_repo(repo)
        git(repo, "remote", "add", "origin", str(remote))
        git(repo, "push", "-q", "origin", "main")
        work = tmp_path / "target-wt"
        git(repo, "worktree", "add", "-q", "-b", BRANCH, str(work))
        (work / "feature").write_text("work\n")
        git(work, "add", "feature")
        git(work, "commit", "-q", "-m", "the feature")
        (work / "new_source.py").write_text("more\n")

        found = check(tmp_path, repo=repo)
        assert os.path.realpath(found["worktree"]) == os.path.realpath(work)
        assert found["unpushed"] == 1
        assert found["uncommitted"] == 1
        assert found["head"] == git(work, "rev-parse", "--short", "HEAD")
        assert "1 uncommitted file(s) and 1 commit(s) no remote has" in found["work"]

    def test_a_branch_no_worktree_holds_is_said_so(self, tmp_path):
        found = check(tmp_path)
        assert found["worktree"] is None
        assert found["work"] == f"No worktree holds {BRANCH}."

    def test_a_story_with_no_branch_has_no_worktree(self, tmp_path):
        found = check(tmp_path, branch=None)
        assert found["branch"] is None
        assert "no branch recorded" in found["work"]


def run_sh():
    with open(RUN_SH) as f:
        return f.read()


class TestRunSh:
    """run.sh itself needs an agent to run, so its wiring is checked as text."""

    def test_the_session_is_resumed_after_the_agent_and_before_the_title_watch_stops(
        self,
    ):
        body = run_sh()
        agent = body.index('--session-id "$SESSION_ID" "$AGENT_PROMPT" </dev/null')
        check_at = body.index(
            'python3 "$SCRIPT_DIR/scripts/iteration-stopped-short.py"'
        )
        resume = body.index('--resume "$SESSION_ID" "$RESUME_PROMPT"')
        stop = body.index("\n  stop_title_watch\n", agent)
        assert agent < check_at < resume < stop

    def test_a_resume_spends_only_what_is_left_of_the_iteration(self):
        body = run_sh()
        assert re.search(r"^ITERATION_SECONDS=\d+$", body, re.MULTILINE)
        assert 'timeout-tree.sh" 7200' not in body
        assert "LEFT=$((ITERATION_SECONDS - ($(date +%s) - BASE_STARTED_AT)))" in body
        assert 'timeout-tree.sh" "$LEFT" $BOT_CLAUDE_BIN' in body

    def test_the_offset_is_taken_before_the_agent_starts(self):
        body = run_sh()
        assert body.index("PROGRESS_OFFSET=") < body.index('"$AGENT_PROMPT" </dev/null')

    def test_a_story_still_short_gets_its_entry_through_the_locked_append(self):
        body = run_sh()
        assert (
            "jq -r '.entry' | \"$SCRIPT_DIR/scripts/append-progress.sh\" --progress-file"
            in body
        )

    def test_a_resume_is_logged_and_appended_to_what_the_completion_check_reads(self):
        body = run_sh()
        assert '{"type":"resume"' in body
        assert 'tee -a "$ITERATION_LOG" >> "$TEMP_OUTPUT"' in body


FAKE_CLAUDE = """#!/bin/bash
# Records each run's flags (the prompt spans lines), then does what FAKE_MODE says a resumed session does.
printf '%s\\n' "${*:1:7}" >> "$FAKE_CALLS"
echo '{"type":"result"}'
case "$FAKE_MODE" in
  record) printf '## 2026-09-25 11:00 - US-313 - Status: pending (iteration ended, no transition)\\n' >> "$PROGRESS_FILE" ;;
  move) jq '(.stories[] | select(.id == "US-313") | .status) = "committed"' "$PRD_FILE" > "$PRD_FILE.new" && mv "$PRD_FILE.new" "$PRD_FILE" ;;
esac
"""


def resume_block():
    """The resume loop exactly as run.sh has it, between the agent run and stop_title_watch."""
    body = run_sh()
    start = body.index('  if [ "$USE_TUI" != true ]; then\n    RESUMED=0\n')
    return body[start : body.index("\n  stop_title_watch\n", start)]


class TestResumeLoop:
    """run.sh's resume loop, run against a fake claude in tmp_path."""

    def run(self, tmp_path, mode="nothing", agent="claude", elapsed=0):
        repo = tmp_path / "target"
        new_repo(repo)
        prd = tmp_path / "prd.json"
        write_prd(prd, "pending")
        progress = tmp_path / "progress.txt"
        progress.write_text(heading("US-200"))
        fake = tmp_path / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        calls = tmp_path / "calls"
        calls.write_text("")
        (tmp_path / "iteration.log").write_text('{"type":"prompt"}\n')
        env = dict(
            os.environ,
            SCRIPT_DIR=os.path.abspath(ROOT_DIR),
            PRD_FILE=str(prd),
            PROGRESS_FILE=str(progress),
            PROGRESS_OFFSET=str(progress.stat().st_size),
            GIT_REPO=str(repo),
            STORY_ID=STORY,
            STORY_STATUS="pending",
            STORY_BRANCH=BRANCH,
            SESSION_ID="0f1e",
            ITERATION_LOG=str(tmp_path / "iteration.log"),
            TEMP_OUTPUT=str(tmp_path / "output"),
            ITERATION_SECONDS="7200",
            USE_TUI="false",
            BOT_AGENT=agent,
            BOT_CLAUDE_BIN=str(fake),
            CLAUDE_MODEL_FLAG="",
            BOT_DIRNAME="brave-dev-loop",
            FAKE_MODE=mode,
            FAKE_CALLS=str(calls),
        )
        env.pop("BOT_COMPARISON_RUN", None)
        script = (
            "set -e\nbot_slot_heartbeat() { :; }\n"
            f"BASE_STARTED_AT=$(( $(date +%s) - {elapsed} ))\n" + resume_block()
        )
        subprocess.run(
            ["bash", "-c", script], check=True, env=env, capture_output=True, text=True
        )
        runs = calls.read_text().splitlines()
        entries = re.findall(
            rf"^## .* - {STORY} - ", progress.read_text(), re.MULTILINE
        )
        return runs, len(entries), (tmp_path / "iteration.log").read_text()

    def test_a_session_that_never_records_is_resumed_twice_then_given_one_entry(
        self, tmp_path
    ):
        runs, entries, log = self.run(tmp_path)
        assert len(runs) == 2
        assert all(r.startswith("--dangerously-skip-permissions --print") for r in runs)
        assert all("--resume 0f1e" in r for r in runs)
        assert entries == 1
        assert log.count('"type":"resume"') == 2

    def test_a_resumed_session_that_records_is_left_alone(self, tmp_path):
        runs, entries, _ = self.run(tmp_path, mode="record")
        assert len(runs) == 1
        assert entries == 1

    def test_a_resumed_session_that_moves_the_story_gets_no_entry(self, tmp_path):
        runs, entries, _ = self.run(tmp_path, mode="move")
        assert len(runs) == 1
        assert entries == 0

    @pytest.mark.parametrize("agent", ["codex", "cursor", "bravebot"])
    def test_an_agent_without_a_session_id_gets_its_entry_unresumed(
        self, tmp_path, agent
    ):
        runs, entries, _ = self.run(tmp_path, agent=agent)
        assert runs == []
        assert entries == 1

    def test_an_iteration_near_its_time_limit_is_not_resumed(self, tmp_path):
        runs, entries, _ = self.run(tmp_path, elapsed=7200 - 300)
        assert runs == []
        assert entries == 1
