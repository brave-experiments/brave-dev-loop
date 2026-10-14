"""run.sh's reviewer gate: waiting for the reviewer's verdict on what it pushed.

scripts/review-verdict.py decides what a review means; scripts/lib/review-gate.sh
decides what the run does about it. The shell half is driven against a copy of
scripts/ with a config of its own and a stub gh, so nothing here reads the
operator's config.json or talks to GitHub.
"""

import importlib.util
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SCRIPTS = os.path.join(ROOT, "scripts")
RUN_SH = os.path.join(ROOT, "run.sh")

spec = importlib.util.spec_from_file_location(
    "review_verdict", os.path.join(SCRIPTS, "review-verdict.py")
)
review_verdict = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_verdict)

REVIEWER = "the-reviewer"
H1, H2, H3 = "a" * 40, "b" * 40, "c" * 40


def review(state, head, who=REVIEWER, at="2026-10-14T10:00:00Z"):
    return {
        "author": {"login": who},
        "state": state,
        "submittedAt": at,
        "commit": {"oid": head},
    }


def pr(reviews=(), head=H2, state="OPEN", requested=(), draft=False):
    return {
        "state": state,
        "isDraft": draft,
        "headRefOid": head,
        "reviews": list(reviews),
        "reviewRequests": [{"login": login} for login in requested],
    }


def verdict_of(pull, threads=0):
    return review_verdict.verdict(pull, REVIEWER, lambda: threads)


class TestVerdict:
    def test_no_review_is_pending(self):
        assert verdict_of(pr())["verdict"] == "pending"

    def test_a_review_of_an_earlier_head_is_not_a_review_of_this_one(self):
        # The reviewer approved the code before the push; the push changed it.
        out = verdict_of(pr([review("APPROVED", H1)], head=H2))
        assert out["verdict"] == "pending"

    def test_an_approval_at_the_head_passes(self):
        assert verdict_of(pr([review("APPROVED", H2)]))["verdict"] == "approved"

    def test_a_request_for_changes_at_the_head_fails(self):
        out = verdict_of(pr([review("CHANGES_REQUESTED", H2)]))
        assert out["verdict"] == "changes"

    def test_the_newest_review_at_the_head_wins(self):
        reviews = [
            review("CHANGES_REQUESTED", H2, at="2026-10-14T10:00:00Z"),
            review("APPROVED", H2, at="2026-10-14T11:00:00Z"),
        ]
        assert verdict_of(pr(reviews))["verdict"] == "approved"

    def test_another_accounts_review_is_not_the_reviewers(self):
        out = verdict_of(pr([review("APPROVED", H2, who="someone-else")]))
        assert out["verdict"] == "pending"

    def test_logins_compare_without_case(self):
        out = verdict_of(pr([review("APPROVED", H2, who=REVIEWER.upper())]))
        assert out["verdict"] == "approved"

    @pytest.mark.parametrize("state", ["PENDING", "DISMISSED"])
    def test_a_review_that_was_never_submitted_or_was_dismissed_says_nothing(
        self, state
    ):
        assert verdict_of(pr([review(state, H2)]))["verdict"] == "pending"

    def test_a_comment_review_that_leaves_threads_open_fails(self):
        out = verdict_of(pr([review("COMMENTED", H2)]), threads=2)
        assert (out["verdict"], out["openThreads"]) == ("changes", 2)

    def test_a_comment_review_that_leaves_none_open_passes(self):
        out = verdict_of(pr([review("COMMENTED", H2)]), threads=0)
        assert out["verdict"] == "approved"

    def test_threads_are_only_read_for_a_comment_review(self):
        def boom():
            raise AssertionError("approved and changes need no thread count")

        for state in ("APPROVED", "CHANGES_REQUESTED"):
            review_verdict.verdict(pr([review(state, H2)]), REVIEWER, boom)

    @pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
    def test_a_pull_request_that_is_not_open_is_closed(self, state):
        out = verdict_of(pr([review("CHANGES_REQUESTED", H2)], state=state))
        assert out["verdict"] == "closed"

    def test_a_draft_nobody_asked_the_reviewer_about_is_not_waited_for(self):
        assert verdict_of(pr(draft=True))["verdict"] == "draft"

    def test_a_draft_the_reviewer_was_asked_about_is_waited_for(self):
        out = verdict_of(pr(draft=True, requested=[REVIEWER]))
        assert (out["verdict"], out["requested"]) == ("pending", True)

    def test_requested_says_whether_the_reviewer_is_still_asked(self):
        assert verdict_of(pr(requested=["x", REVIEWER]))["requested"] is True
        assert verdict_of(pr(requested=["x"]))["requested"] is False


class TestOpenThreads:
    @staticmethod
    def page(threads, next_cursor=None):
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {
                                    "hasNextPage": next_cursor is not None,
                                    "endCursor": next_cursor,
                                },
                                "nodes": [
                                    {
                                        "isResolved": resolved,
                                        "comments": {
                                            "nodes": [{"author": {"login": who}}]
                                        },
                                    }
                                    for who, resolved in threads
                                ],
                            }
                        }
                    }
                }
            }
        )

    def test_counts_unresolved_threads_the_reviewer_started(self, monkeypatch):
        pages = [
            self.page([(REVIEWER, False), (REVIEWER, True), ("someone-else", False)])
        ]
        monkeypatch.setattr(review_verdict, "gh", lambda *a: pages.pop(0))
        assert review_verdict.open_threads_by("o/r", 7, REVIEWER) == 1

    def test_reads_every_page(self, monkeypatch):
        asked = []
        pages = [
            self.page([(REVIEWER, True)], next_cursor="C1"),
            self.page([(REVIEWER, False)]),
        ]

        def fake_gh(*args):
            asked.append(args)
            return pages.pop(0)

        monkeypatch.setattr(review_verdict, "gh", fake_gh)
        assert review_verdict.open_threads_by("o/r", 7, REVIEWER) == 1
        assert "after=C1" in asked[1]

    def test_unreadable_threads_are_an_error_not_zero(self, monkeypatch):
        monkeypatch.setattr(review_verdict, "gh", lambda *a: "not json")
        with pytest.raises(review_verdict.GitHubError):
            review_verdict.open_threads_by("o/r", 7, REVIEWER)


GH_STUB = """#!/usr/bin/env python3
import json, os, shutil, subprocess, sys
d = os.environ["GH_STUB_DIR"]
args = sys.argv[1:]
with open(d + "/calls.log", "a") as f:
    f.write(" ".join(args) + "\\n")
if args[:2] == ["pr", "view"]:
    pull = open(d + "/pr.json").read()
    if "--jq" in args:
        sys.stdout.write(subprocess.run(
            ["jq", "-r", args[args.index("--jq") + 1]], input=pull,
            capture_output=True, text=True).stdout)
    else:
        sys.stdout.write(pull)
elif args[:2] == ["api", "graphql"]:
    sys.stdout.write(open(d + "/threads.json").read())
elif any("requested_reviewers" in a for a in args):
    # The reviewer answers as soon as it is asked, if the test says it will.
    if os.path.exists(d + "/after_request.json"):
        shutil.copy(d + "/after_request.json", d + "/pr.json")
else:
    sys.exit(1)
"""


class Gate:
    """A throwaway bot directory, a stub gh, and a way to run the gate in it."""

    def __init__(self, tmp_path, max_rounds=5, reviewer=REVIEWER):
        self.bot = tmp_path / "bot"
        shutil.copytree(
            SCRIPTS, self.bot / "scripts", ignore=shutil.ignore_patterns("__pycache__")
        )
        (self.bot / "projects").mkdir()
        self.config = self.bot / "config.json"
        self.config.write_text(
            json.dumps(
                {
                    "project": {
                        "name": "p",
                        "org": "o",
                        "prRepository": "o/r",
                        "issueRepository": "o/r",
                        "defaultBranch": "main",
                        "targetRepoPath": "../p",
                        "profile": "default",
                    },
                    "bot": {"username": "the-bot"},
                    "reviewer": {
                        "username": reviewer,
                        "maxRounds": max_rounds,
                        "waitMinutes": 1,
                    },
                    "bestPractices": {"docsDir": "../p/docs"},
                }
            )
        )
        self.stubs = tmp_path / "stubs"
        self.stubs.mkdir()
        stub = self.stubs / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)
        self.dir = tmp_path / "gh-state"
        self.dir.mkdir()
        self.set_pr(pr(requested=[REVIEWER]))
        (self.dir / "threads.json").write_text(TestOpenThreads.page([]))

    def set_pr(self, pull):
        (self.dir / "pr.json").write_text(json.dumps(pull))

    def reviewer_answers_when_asked(self, pull):
        (self.dir / "after_request.json").write_text(json.dumps(pull))

    @property
    def calls(self):
        log = self.dir / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    @property
    def requests(self):
        return [c for c in self.calls if "requested_reviewers" in c]

    def run(self, script, wait_seconds=2):
        full = (
            f"source {self.bot}/scripts/lib/load-config.sh\n"
            f"source {self.bot}/scripts/lib/review-gate.sh\n"
            'report() { echo "pin=$BOT_REVIEW_PIN round=$BOT_REVIEW_ROUND '
            'story=$BOT_REVIEW_STORY verdict=$BOT_REVIEW_VERDICT"; }\n'
            'set_head() { jq --arg h "$1" \'.headRefOid = $h | '
            ".reviews |= map(.commit.oid = $h)' "
            '"$GH_STUB_DIR/pr.json" > "$GH_STUB_DIR/t" && '
            'mv "$GH_STUB_DIR/t" "$GH_STUB_DIR/pr.json"; }\n'
            f"{script}"
        )
        return subprocess.run(
            ["bash", "-c", full],
            capture_output=True,
            text=True,
            timeout=60,
            env={
                **os.environ,
                "PATH": f"{self.stubs}{os.pathsep}{os.environ['PATH']}",
                "GH_STUB_DIR": str(self.dir),
                "BOT_CONFIG_FILE": str(self.config),
                "BOT_REVIEW_POLL_SECONDS": "1",
                "BOT_REVIEW_WAIT_SECONDS": str(wait_seconds),
            },
        )


@pytest.fixture
def gate(tmp_path):
    return Gate(tmp_path)


def after(story="US-1", pr_number="7", before=H1, status="pushed"):
    return (
        f"bot_review_after_iteration {story} '{pr_number}' '{before}' {status}; report"
    )


class TestGate:
    def test_an_approval_ends_the_wait_and_asks_for_nothing(self, gate):
        gate.set_pr(pr([review("APPROVED", H2)]))
        out = gate.run(after())
        assert out.returncode == 0, out.stderr
        assert "pin= round=0 story= verdict=approved" in out.stdout
        assert gate.requests == []

    def test_a_request_for_changes_pins_the_story_for_a_fix_round(self, gate):
        gate.set_pr(pr([review("CHANGES_REQUESTED", H2)]))
        out = gate.run(after())
        assert "pin=US-1 round=1 story=US-1 verdict=changes" in out.stdout

    def test_a_reviewer_nobody_asked_is_asked_once_and_then_waited_for(self, gate):
        gate.set_pr(pr())
        gate.reviewer_answers_when_asked(pr([review("APPROVED", H2)]))
        out = gate.run(after())
        assert "verdict=approved" in out.stdout
        assert len(gate.requests) == 1
        assert f"reviewers[]={REVIEWER}" in gate.requests[0]

    def test_a_reviewer_already_asked_is_not_asked_again(self, gate):
        gate.set_pr(pr(requested=[REVIEWER]))
        gate.run(after())
        assert gate.requests == []

    def test_no_review_in_time_moves_on_and_leaves_the_story_alone(self, gate):
        gate.set_pr(pr(requested=[REVIEWER]))
        out = gate.run(after(), wait_seconds=2)
        assert "did not review PR #7" in out.stdout
        assert "pin= round=0 story=" in out.stdout

    def test_a_pull_request_that_did_not_move_is_never_waited_for(self, gate):
        gate.set_pr(pr(head=H1))
        out = gate.run(after(before=H1))
        assert "waiting for" not in out.stdout
        assert not any("reviews" in c for c in gate.calls)

    def test_a_new_pull_request_is_waited_for(self, gate):
        gate.set_pr(pr([review("APPROVED", H2)]))
        out = gate.run(after(before=""))
        assert "verdict=approved" in out.stdout

    def test_a_head_github_would_not_give_is_left_alone(self, gate):
        out = gate.run(after(before="?"))
        assert "waiting for" not in out.stdout

    @pytest.mark.parametrize("status", ["pending", "committed", "merged", "skipped"])
    def test_only_a_pushed_story_is_waited_for(self, gate, status):
        out = gate.run(after(status=status))
        assert "waiting for" not in out.stdout
        assert gate.calls == []

    def test_a_story_with_no_pull_request_is_left_alone(self, gate):
        out = gate.run(after(pr_number=""))
        assert gate.calls == []
        assert "pin= round=0" in out.stdout

    def test_a_merged_pull_request_ends_the_wait(self, gate):
        gate.set_pr(pr(state="MERGED"))
        out = gate.run(after())
        assert "no longer open" in out.stdout

    def test_a_draft_is_not_waited_for(self, gate):
        gate.set_pr(pr(draft=True))
        out = gate.run(after())
        assert "is a draft" in out.stdout
        assert gate.requests == []

    def test_it_gives_up_after_max_rounds_reviews(self, tmp_path):
        gate = Gate(tmp_path, max_rounds=3)
        gate.set_pr(pr([review("CHANGES_REQUESTED", H2)]))
        out = gate.run(
            after(before=H1)
            + f"; set_head {H3}; "
            + after(before=H2)
            + f"; set_head {'d' * 40}; "
            + after(before=H3)
        )
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("pin=")]
        assert lines == [
            "pin=US-1 round=1 story=US-1 verdict=changes",
            "pin=US-1 round=2 story=US-1 verdict=changes",
            "pin= round=0 story= verdict=changes",
        ]
        assert "after 3 reviews. Giving up" in out.stdout

    def test_a_fix_round_that_pushes_nothing_ends_the_loop(self, gate):
        gate.set_pr(pr([review("CHANGES_REQUESTED", H2)]))
        out = gate.run(after(before=H1) + "; " + after(before=H2))
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("pin=")]
        assert lines[-1] == "pin= round=0 story= verdict=changes"
        assert "pushed nothing" in out.stdout

    def test_a_different_story_starts_its_own_count(self, gate):
        gate.set_pr(pr([review("CHANGES_REQUESTED", H2)]))
        out = gate.run(after("US-1") + f"; set_head {H3}; " + after("US-2", before=H2))
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("pin=")]
        assert lines[-1] == "pin=US-2 round=1 story=US-2 verdict=changes"

    def test_a_story_that_leaves_pushed_forgets_the_count(self, gate):
        gate.set_pr(pr([review("CHANGES_REQUESTED", H2)]))
        out = gate.run(after() + "; " + after(status="merged"))
        lines = [ln for ln in out.stdout.splitlines() if ln.startswith("pin=")]
        assert lines[-1] == "pin= round=0 story= verdict=changes"

    def test_max_rounds_zero_turns_it_off(self, tmp_path):
        gate = Gate(tmp_path, max_rounds=0)
        out = gate.run(after())
        assert gate.calls == []
        assert "waiting for" not in out.stdout

    def test_no_reviewer_turns_it_off(self, tmp_path):
        gate = Gate(tmp_path, reviewer=None)
        out = gate.run(after())
        assert gate.calls == []
        assert "waiting for" not in out.stdout

    def test_the_wait_writes_to_the_iteration_log_so_status_does_not_read_a_hang(
        self, gate, tmp_path
    ):
        log = tmp_path / "iteration.log"
        log.write_text("")
        gate.set_pr(pr(requested=[REVIEWER]))
        gate.run(f"bot_review_after_iteration US-1 7 {H1} pushed {log}")
        records = [json.loads(ln) for ln in log.read_text().splitlines()]
        assert records and records[0]["type"] == "review-wait"
        assert records[0]["pr"] == 7

    def test_no_log_is_created_when_the_iteration_has_none(self, gate, tmp_path):
        gate.set_pr(pr(requested=[REVIEWER]))
        gate.run(after())
        assert list(tmp_path.glob("*.log")) == []


class TestConfig:
    @staticmethod
    def validate(tmp_path, rounds, minutes, reviewer=REVIEWER):
        # A directory per call: the Gate copies scripts/ into it.
        where = tmp_path / f"r{rounds}m{minutes}{'on' if reviewer else 'off'}"
        where.mkdir()
        gate = Gate(where, reviewer=reviewer)
        return gate.run(
            f"BOT_REVIEW_MAX_ROUNDS='{rounds}'; BOT_REVIEW_WAIT_MINUTES='{minutes}'; "
            "bot_validate_review_config; echo rc=$?"
        )

    def test_the_defaults_are_five_reviews_and_two_hours(self, tmp_path):
        gate = Gate(tmp_path)
        config = json.loads(gate.config.read_text())
        del config["reviewer"]["maxRounds"], config["reviewer"]["waitMinutes"]
        gate.config.write_text(json.dumps(config))
        out = gate.run('echo "$BOT_REVIEW_MAX_ROUNDS $BOT_REVIEW_WAIT_MINUTES"')
        assert out.stdout.strip() == "5 120"

    @pytest.mark.parametrize("rounds,minutes", [("x", "5"), ("-1", "5"), ("3", "soon")])
    def test_a_value_that_is_not_a_count_is_refused(self, tmp_path, rounds, minutes):
        out = self.validate(tmp_path, rounds, minutes)
        assert "rc=1" in out.stdout
        assert "reviewer." in out.stderr

    def test_a_zero_wait_is_refused_while_the_gate_is_on(self, tmp_path):
        out = self.validate(tmp_path, 5, 0)
        assert "rc=1" in out.stdout

    def test_a_zero_wait_is_fine_while_the_gate_is_off(self, tmp_path):
        assert "rc=0" in self.validate(tmp_path, 0, 0).stdout
        assert "rc=0" in self.validate(tmp_path, 5, 0, reviewer=None).stdout


class TestRunSh:
    """run.sh is exercised by hand against tests/sim; these pin the wiring."""

    @staticmethod
    def body():
        with open(RUN_SH) as f:
            return f.read()

    def test_the_gate_runs_after_the_comparison_and_before_the_claim_is_released(
        self,
    ):
        body = self.body()
        gate = body.index("bot_review_after_iteration")
        assert body.index("scripts/comparison-evaluate.sh") < gate
        assert body.index("\n  release_claim\n", gate) - gate < 1500

    def test_a_fix_round_is_not_charged_to_the_iteration_budget(self):
        body = self.body()
        gate = body.index("bot_review_after_iteration")
        assert "MAX_ITERATIONS=$((MAX_ITERATIONS + 1))" in body[gate : gate + 400]

    def test_selection_names_the_pinned_story_and_falls_back_to_the_usual(self):
        body = self.body()
        assert 'select_story "${BOT_REVIEW_PIN:-$EXTRA_PROMPT}"' in body
        assert 'select_story "$EXTRA_PROMPT"' in body

    def test_the_head_is_read_before_the_agent_starts(self):
        body = self.body()
        before = body.index("REVIEW_HEAD_BEFORE=$(bot_pr_head")
        assert before < body.index('"$SCRIPT_DIR/scripts/exec-clean.sh" --cd')

    def test_a_terminal_session_gets_no_log_written_for_it(self):
        body = self.body()
        start = body.index("GATE_LOG=")
        assert 'if [ "$USE_TUI" != true ]' in body[start : start + 120]
