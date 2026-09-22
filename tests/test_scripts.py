"""Tests for all Python scripts in brave-dev-loop.

Covers: update-prd-status.py, select-task.py, business-hours-elapsed.py,
and check-prd-has-work.py.
"""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPT_DIR = os.path.join(REPO_ROOT, "scripts")
PROJECTS_DIR = os.path.join(REPO_ROOT, "projects")
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
UPDATE_PRD_SCRIPT = os.path.join(SCRIPT_DIR, "update-prd-status.py")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _reference_configs():
    """The tracked config templates, newest deployment included automatically.

    The glob deliberately misses `config.json`: that is one machine's live
    configuration, gitignored, and a test that read it would pass or fail on
    whichever project the machine happens to be running."""
    return [
        name
        for name in sorted(os.listdir(REPO_ROOT))
        if fnmatch.fnmatch(name, "config.*.json")
    ]


def make_story(status="pending", id="US-001", priority=1, **overrides):
    base = {
        "id": id,
        "title": f"Story {id}",
        "status": status,
        "priority": priority,
        "branchName": None,
        "prNumber": None,
        "prUrl": None,
        "lastActivityBy": None,
    }
    base.update(overrides)
    return base


def empty_run_state(**overrides):
    base = {"storiesCheckedThisRun": []}
    base.update(overrides)
    return base


def run_update_script(prd_path, run_state_path, *args):
    """Run update-prd-status.py as a subprocess."""
    cmd = [
        sys.executable,
        UPDATE_PRD_SCRIPT,
        "--prd",
        prd_path,
        "--run-state",
        run_state_path,
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


# ═══════════════════════════════════════════════════════════════════════════
# update-prd-status.py
# ═══════════════════════════════════════════════════════════════════════════


class TestUpdatePrdValidation:
    def test_committed_requires_pending(self, update_prd_status):
        assert (
            update_prd_status.validate_transition("committed", make_story("pending"))
            is None
        )
        assert (
            update_prd_status.validate_transition("committed", make_story("pushed"))
            is not None
        )

    def test_pushed_requires_committed(self, update_prd_status):
        assert (
            update_prd_status.validate_transition("pushed", make_story("committed"))
            is None
        )
        assert (
            update_prd_status.validate_transition("pushed", make_story("pending"))
            is not None
        )

    def test_merged_requires_pushed(self, update_prd_status):
        assert (
            update_prd_status.validate_transition("merged", make_story("pushed"))
            is None
        )
        assert (
            update_prd_status.validate_transition("merged", make_story("pending"))
            is not None
        )

    def test_skipped_allowed_from_any_active(self, update_prd_status):
        for status in ("pending", "committed", "pushed"):
            assert (
                update_prd_status.validate_transition("skipped", make_story(status))
                is None
            )

    def test_skipped_rejected_from_terminal(self, update_prd_status):
        for status in ("merged", "skipped", "invalid"):
            assert (
                update_prd_status.validate_transition("skipped", make_story(status))
                is not None
            )

    def test_invalid_rejected_from_terminal(self, update_prd_status):
        assert (
            update_prd_status.validate_transition("invalid", make_story("invalid"))
            is not None
        )

    def test_set_activity_requires_pushed(self, update_prd_status):
        assert (
            update_prd_status.validate_transition("set-activity", make_story("pushed"))
            is None
        )
        assert (
            update_prd_status.validate_transition("set-activity", make_story("pending"))
            is not None
        )

    def test_set_branch_allows_pending_or_committed(self, update_prd_status):
        assert (
            update_prd_status.validate_transition("set-branch", make_story("pending"))
            is None
        )
        assert (
            update_prd_status.validate_transition("set-branch", make_story("committed"))
            is None
        )
        assert (
            update_prd_status.validate_transition("set-branch", make_story("pushed"))
            is not None
        )

    def test_merged_is_terminal(self, update_prd_status):
        """A merged story is finished with: nothing may move it anywhere."""
        story = make_story("merged")
        assert update_prd_status.validate_transition("skipped", story) is not None
        assert update_prd_status.validate_transition("invalid", story) is not None
        assert update_prd_status.validate_transition("merged", story) is not None


class TestUpdatePrdHandlers:
    def test_committed(self, update_prd_status):
        story = make_story("pending")
        update_prd_status.handle_committed(story, Namespace(branch="fix-test"))
        assert story["status"] == "committed"
        assert story["lastActivityBy"] is None
        assert story["branchName"] == "fix-test"

    def test_pushed(self, update_prd_status):
        story = make_story("committed")
        update_prd_status.handle_pushed(story, Namespace(pr_number=34567))
        assert story["status"] == "pushed"
        assert story["prNumber"] == 34567
        assert story["prUrl"].endswith("/pull/34567")
        assert story["lastActivityBy"] == "bot"

    def test_merged(self, update_prd_status):
        story = make_story("pushed")
        update_prd_status.handle_merged(story, Namespace())
        assert story["status"] == "merged"
        assert story["mergedAt"] is not None
        assert "nextMergedCheck" not in story

    def test_skipped(self, update_prd_status):
        story = make_story("pending")
        update_prd_status.handle_skipped(story, Namespace(reason="duplicate"))
        assert story["status"] == "skipped"
        assert story["skipReason"] == "duplicate"

    def test_invalid(self, update_prd_status):
        story = make_story("pushed")
        update_prd_status.handle_invalid(story, Namespace(reason="PR closed"))
        assert story["status"] == "invalid"
        assert story["skipReason"] == "PR closed"

    def test_set_activity(self, update_prd_status):
        story = make_story("pushed", lastActivityBy="bot")
        update_prd_status.handle_set_activity(story, Namespace(who="reviewer"))
        assert story["lastActivityBy"] == "reviewer"

    def test_set_ping(self, update_prd_status):
        story = make_story("pushed")
        update_prd_status.handle_set_ping(story, Namespace())
        assert "lastReviewerPing" in story

    def test_set_branch(self, update_prd_status):
        story = make_story("pending")
        update_prd_status.handle_set_branch(story, Namespace(branch="fix-new"))
        assert story["branchName"] == "fix-new"


class TestUpdatePrdStateChange:
    def test_status_transitions_are_state_changes(self, update_prd_status):
        for cmd in ("committed", "pushed", "merged", "skipped", "invalid"):
            assert update_prd_status.is_state_change(cmd, Namespace()) is True

    def test_set_activity_bot_is_state_change(self, update_prd_status):
        assert (
            update_prd_status.is_state_change("set-activity", Namespace(who="bot"))
            is True
        )

    def test_set_activity_reviewer_not_state_change(self, update_prd_status):
        assert (
            update_prd_status.is_state_change("set-activity", Namespace(who="reviewer"))
            is False
        )

    def test_non_status_commands_not_state_changes(self, update_prd_status):
        for cmd in ("set-ping", "set-branch"):
            assert update_prd_status.is_state_change(cmd, Namespace()) is False


class TestUpdatePrdIntegration:
    def test_full_lifecycle(self, write_json, read_json, tmp_dir):
        prd_path = write_json("prd.json", {"stories": [make_story("pending")]})
        rs_path = write_json("run-state.json", {"lastIterationHadStateChange": False})

        rc, _, _ = run_update_script(
            prd_path, rs_path, "committed", "US-001", "--branch", "fix-x"
        )
        assert rc == 0
        assert read_json(prd_path)["stories"][0]["status"] == "committed"

        rc, _, _ = run_update_script(
            prd_path, rs_path, "pushed", "US-001", "--pr-number", "123"
        )
        assert rc == 0
        assert read_json(prd_path)["stories"][0]["prNumber"] == 123

        rc, _, _ = run_update_script(prd_path, rs_path, "merged", "US-001")
        assert rc == 0
        assert read_json(prd_path)["stories"][0]["status"] == "merged"
        assert read_json(rs_path)["lastIterationHadStateChange"] is True

    def test_invalid_transition_exits_1(self, write_json, tmp_dir):
        prd_path = write_json("prd.json", {"stories": [make_story("pending")]})
        rs_path = write_json("run-state.json", {})
        rc, _, err = run_update_script(prd_path, rs_path, "merged", "US-001")
        assert rc == 1
        assert "Validation error" in err

    def test_story_not_found_exits_1(self, write_json, tmp_dir):
        prd_path = write_json("prd.json", {"stories": [make_story("pending")]})
        rs_path = write_json("run-state.json", {})
        rc, _, err = run_update_script(
            prd_path, rs_path, "committed", "US-999", "--branch", "x"
        )
        assert rc == 1
        assert "not found" in err

    def test_missing_prd_exits_2(self, tmp_dir):
        rc, _, _ = run_update_script(
            os.path.join(tmp_dir, "nope.json"),
            os.path.join(tmp_dir, "rs.json"),
            "committed",
            "US-001",
            "--branch",
            "x",
        )
        assert rc == 2

    def test_special_chars_in_reason(self, write_json, read_json, tmp_dir):
        prd_path = write_json("prd.json", {"stories": [make_story("pending")]})
        rs_path = write_json("run-state.json", {})
        reason = 'PR #123 already exists for "this issue"'
        rc, _, _ = run_update_script(
            prd_path, rs_path, "skipped", "US-001", "--reason", reason
        )
        assert rc == 0
        assert read_json(prd_path)["stories"][0]["skipReason"] == reason

    def test_other_stories_untouched(self, write_json, read_json, tmp_dir):
        prd_path = write_json(
            "prd.json",
            {
                "stories": [
                    make_story("pending", id="US-001"),
                    make_story("pushed", id="US-002"),
                ],
            },
        )
        rs_path = write_json("run-state.json", {})
        run_update_script(prd_path, rs_path, "committed", "US-001", "--branch", "fix-a")
        prd = read_json(prd_path)
        assert prd["stories"][0]["status"] == "committed"
        assert prd["stories"][1]["status"] == "pushed"

    def test_missing_run_state_still_succeeds(self, write_json, read_json, tmp_dir):
        prd_path = write_json("prd.json", {"stories": [make_story("pending")]})
        rc, _, _ = run_update_script(
            prd_path,
            os.path.join(tmp_dir, "nope-rs.json"),
            "committed",
            "US-001",
            "--branch",
            "fix-x",
        )
        assert rc == 0
        assert read_json(prd_path)["stories"][0]["status"] == "committed"


# ═══════════════════════════════════════════════════════════════════════════
# select-task.py
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectTaskParseIso:
    def test_valid_z_suffix(self, select_task):
        dt = select_task.parse_iso("2026-01-15T10:30:00Z")
        assert dt.year == 2026 and dt.month == 1 and dt.hour == 10

    def test_none_returns_epoch(self, select_task):
        assert select_task.parse_iso(None) == select_task.EPOCH

    def test_empty_returns_epoch(self, select_task):
        assert select_task.parse_iso("") == select_task.EPOCH

    def test_invalid_returns_epoch(self, select_task):
        assert select_task.parse_iso("not-a-date") == select_task.EPOCH


class TestSelectTaskIssueNumber:
    def test_parses_description_reference(self, select_task):
        story = {"description": "Resolve issue #133: rename the thing"}
        assert select_task.issue_number(story) == 133

    def test_none_without_reference(self, select_task):
        assert select_task.issue_number({"description": "no reference here"}) is None

    def test_none_without_description(self, select_task):
        assert select_task.issue_number({}) is None


class TestSelectTaskTiers:
    def test_pushed_reviewer_is_urgent(self, select_task):
        assert (
            select_task.assign_tier(make_story("pushed", lastActivityBy="reviewer"))
            == select_task.TIER_URGENT
        )

    def test_pushed_bot_is_medium(self, select_task):
        # MEDIUM requires the pushed story to have been processed within the last
        # day; without a recent lastProcessedDate it reads as STALE.
        recent = datetime.now(timezone.utc).isoformat()
        assert (
            select_task.assign_tier(
                make_story("pushed", lastActivityBy="bot", lastProcessedDate=recent)
            )
            == select_task.TIER_MEDIUM
        )

    def test_committed_is_high(self, select_task):
        assert select_task.assign_tier(make_story("committed")) == select_task.TIER_HIGH

    def test_pending_is_normal(self, select_task):
        assert select_task.assign_tier(make_story("pending")) == select_task.TIER_NORMAL


class TestSelectTaskFilter:
    def test_excludes_terminal_statuses(self, select_task):
        stories = [
            make_story("skipped", id="US-001"),
            make_story("invalid", id="US-002"),
            make_story("merged", id="US-003"),
            make_story("pending", id="US-004"),
        ]
        result = select_task.filter_stories(stories, empty_run_state())
        assert [s["id"] for s in result] == ["US-004"]

    def test_excludes_already_checked(self, select_task):
        stories = [
            make_story("pending", id="US-001"),
            make_story("pending", id="US-002"),
        ]
        result = select_task.filter_stories(
            stories, empty_run_state(storiesCheckedThisRun=["US-001"])
        )
        assert [s["id"] for s in result] == ["US-002"]

    def test_skip_pushed_flag(self, select_task):
        stories = [
            make_story("pushed", id="US-001"),
            make_story("pending", id="US-002"),
        ]
        result = select_task.filter_stories(
            stories, empty_run_state(skipPushedTasks=True)
        )
        assert [s["id"] for s in result] == ["US-002"]

    def test_excludes_an_issue_another_machine_is_working(self, select_task):
        stories = [
            make_story("pending", id="US-001", description="Resolve issue #101"),
            make_story("pending", id="US-002", description="Resolve issue #102"),
        ]
        result = select_task.filter_stories(
            stories, empty_run_state(), in_progress={101}
        )
        assert [s["id"] for s in result] == ["US-002"]

    def test_a_story_naming_no_issue_is_never_in_progress(self, select_task):
        stories = [make_story("pending", id="US-001", description="no issue here")]
        result = select_task.filter_stories(
            stories, empty_run_state(), in_progress={101}
        )
        assert [s["id"] for s in result] == ["US-001"]


class TestSelectTaskSortKey:
    def test_urgent_before_normal(self, select_task):
        stories = [
            make_story("pending", id="US-002"),
            make_story("pushed", id="US-001", lastActivityBy="reviewer"),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-001"

    def test_priority_breaks_ties(self, select_task):
        stories = [
            make_story("pending", id="US-001", priority=10),
            make_story("pending", id="US-002", priority=1),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-002"

    def test_pushed_sorted_by_last_processed(self, select_task):
        stories = [
            make_story("pushed", id="US-002", lastProcessedDate="2026-01-02T00:00:00Z"),
            make_story("pushed", id="US-001", lastProcessedDate="2026-01-01T00:00:00Z"),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-001"

    def test_pending_fewer_attempts_first(self, select_task):
        # A fresh pending story (no logs) is worked before one that has been
        # retried, even if the stuck one has a lower priority number.
        stories = [
            make_story(
                "pending",
                id="US-STUCK",
                priority=1,
                iterationLogs=["a", "b", "c"],
            ),
            make_story("pending", id="US-FRESH", priority=50),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-FRESH"

    def test_promote_pending_beats_stale(self, select_task):
        # On the reserved (first) slot, pending work outranks stale pushed
        # maintenance...
        pending = make_story("pending", id="US-P")
        stale = make_story(
            "pushed", id="US-S", lastProcessedDate="2000-01-01T00:00:00Z"
        )
        assert select_task.sort_key(
            pending, promote_pending=True
        ) < select_task.sort_key(stale, promote_pending=True)
        # ...but without promotion the stale PR (tier 3) still outranks pending
        # (tier 4).
        assert select_task.sort_key(stale) < select_task.sort_key(pending)

    def test_promote_pending_still_below_urgent(self, select_task):
        # Reviewer responses (URGENT) are not preempted by the reserved slot.
        pending = make_story("pending", id="US-P")
        urgent = make_story("pushed", id="US-U", lastActivityBy="reviewer")
        assert select_task.sort_key(
            urgent, promote_pending=True
        ) < select_task.sort_key(pending, promote_pending=True)


class TestSelectTaskTriageOrder:
    """Pending work is ordered by the triage axes the issue carries. An
    unlabelled backlog has to sort exactly as it did before the axes existed,
    because most projects never label anything."""

    def test_more_urgent_first(self, select_task):
        stories = [
            make_story("pending", id="US-LATER", triage={"urgency": 4}),
            make_story("pending", id="US-NOW", triage={"urgency": 2}),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-NOW"

    def test_urgency_interrupts_importance(self, select_task):
        """The two axes are separate on purpose: urgency is the interrupt, so
        it decides first even against work that matters more."""
        stories = [
            make_story(
                "pending", id="US-BIGGER", triage={"urgency": 3, "importance": 1}
            ),
            make_story(
                "pending", id="US-SOONER", triage={"urgency": 2, "importance": 5}
            ),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-SOONER"

    def test_importance_orders_equal_urgency(self, select_task):
        stories = [
            make_story(
                "pending", id="US-MINOR", triage={"urgency": 3, "importance": 4}
            ),
            make_story(
                "pending", id="US-MAJOR", triage={"urgency": 3, "importance": 1}
            ),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-MAJOR"

    def test_unjudged_sorts_between_judged(self, select_task):
        """A missing axis is not a low value, so an unlabelled story neither
        jumps the queue nor is buried by it."""
        stories = [
            make_story("pending", id="US-LOW", triage={"urgency": 5}),
            make_story("pending", id="US-NONE"),
            make_story("pending", id="US-HIGH", triage={"urgency": 1}),
        ]
        stories.sort(key=select_task.sort_key)
        assert [s["id"] for s in stories] == ["US-HIGH", "US-NONE", "US-LOW"]

    def test_axes_outrank_attempt_count(self, select_task):
        """Attempt count breaks ties inside an axis band. It must not put a
        cosmetic issue ahead of an urgent one just for being untouched --
        MAX_PENDING_ATTEMPTS is what catches a story that is truly stuck."""
        stories = [
            make_story("pending", id="US-COSMETIC", triage={"urgency": 5}),
            make_story(
                "pending", id="US-URGENT", triage={"urgency": 1}, iterationLogs=["a"]
            ),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-URGENT"

    def test_size_does_not_order_anything(self, select_task):
        """Size says what fits in the time available. Ordering by it would bury
        exactly the large important work that needs splitting."""
        small = make_story("pending", id="US-S", priority=2, triage={"size": 1})
        large = make_story("pending", id="US-L", priority=1, triage={"size": 5})
        stories = [small, large]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-L"

    def test_unlabelled_backlog_sorts_by_priority_as_before(self, select_task):
        stories = [
            make_story("pending", id="US-002", priority=10),
            make_story("pending", id="US-001", priority=1),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-001"

    def test_pushed_maintenance_keeps_its_round_robin(self, select_task):
        """The pushed queue exists so every open PR is looked at in turn. Axes
        there would leave the least important PR waiting for review forever."""
        stories = [
            make_story(
                "pushed",
                id="US-URGENT-PR",
                lastProcessedDate="2026-01-02T00:00:00Z",
                triage={"urgency": 1},
            ),
            make_story(
                "pushed",
                id="US-OLDEST-PR",
                lastProcessedDate="2026-01-01T00:00:00Z",
                triage={"urgency": 5},
            ),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-OLDEST-PR"

    def test_nonsense_axis_value_does_not_raise(self, select_task):
        """data/prd.json is hand-editable, so a run must survive anything in
        the block rather than dying mid-selection."""
        stories = [
            make_story("pending", id="US-BAD", triage={"urgency": "very"}),
            make_story("pending", id="US-OK", triage={"urgency": 1}),
        ]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-OK"

    def test_reviewer_response_still_preempts_an_urgent_pending(self, select_task):
        """The axes order pending work inside its tier. They do not let it
        overtake a reviewer who is waiting on an answer."""
        pending = make_story("pending", id="US-P", triage={"urgency": 1})
        urgent = make_story("pushed", id="US-U", lastActivityBy="reviewer")
        assert select_task.sort_key(urgent) < select_task.sort_key(pending)


class TestSelectTaskQuarantine:
    def test_stuck_pending_is_quarantined(self, select_task):
        story = make_story(
            "pending", iterationLogs=["l"] * select_task.MAX_PENDING_ATTEMPTS
        )
        assert select_task.assign_tier(story) == select_task.TIER_QUARANTINE

    def test_under_cap_stays_normal(self, select_task):
        story = make_story(
            "pending", iterationLogs=["l"] * (select_task.MAX_PENDING_ATTEMPTS - 1)
        )
        assert select_task.assign_tier(story) == select_task.TIER_NORMAL

    def test_quarantined_loses_to_fresh_pending(self, select_task):
        stuck = make_story(
            "pending",
            id="US-STUCK",
            priority=1,
            iterationLogs=["l"] * select_task.MAX_PENDING_ATTEMPTS,
        )
        fresh = make_story("pending", id="US-FRESH", priority=99)
        stories = [stuck, fresh]
        stories.sort(key=select_task.sort_key)
        assert stories[0]["id"] == "US-FRESH"


class TestSelectTaskUpdatePrd:
    def test_sets_last_processed_for_pushed(self, select_task, write_json, read_json):
        story = make_story("pushed")
        prd = {"stories": [story]}
        path = write_json("prd.json", prd)
        select_task.update_prd(path, prd, story, None)
        assert "lastProcessedDate" in read_json(path)["stories"][0]

    def test_skips_last_processed_for_pending(self, select_task, write_json, read_json):
        story = make_story("pending")
        prd = {"stories": [story]}
        path = write_json("prd.json", prd)
        select_task.update_prd(path, prd, story, None)
        assert "lastProcessedDate" not in read_json(path)["stories"][0]

    def test_appends_iteration_log(self, select_task, write_json, read_json):
        story = make_story("pending")
        prd = {"stories": [story]}
        path = write_json("prd.json", prd)
        select_task.update_prd(path, prd, story, "/tmp/log1.txt")
        select_task.update_prd(path, prd, story, "/tmp/log2.txt")
        assert read_json(path)["stories"][0]["iterationLogs"] == [
            "/tmp/log1.txt",
            "/tmp/log2.txt",
        ]


class TestSelectTaskExplicitTarget:
    """What counts as naming a story outright, and what stays a hint."""

    def test_issue_url(self, select_task):
        target = select_task.explicit_target(
            "https://github.com/brave/bravebot/issues/613"
        )
        assert target == ("ref", 613)

    def test_hash_number(self, select_task):
        assert select_task.explicit_target("please do #613") == ("ref", 613)

    def test_pull_request_url(self, select_task):
        target = select_task.explicit_target("https://github.com/o/r/pull/617")
        assert target == ("ref", 617)

    def test_story_id_any_case(self, select_task):
        assert select_task.explicit_target("work us-212") == ("story", "US-212")

    def test_a_story_id_outranks_a_number_beside_it(self, select_task):
        assert select_task.explicit_target("US-212, the #613 one") == (
            "story",
            "US-212",
        )

    def test_prose_with_a_bare_number_names_nothing(self, select_task):
        """A number the operator did not mark as a reference is not a target:
        guessing wrong is the whole thing this exists to prevent."""
        assert select_task.explicit_target("work 2 small ones") is None

    def test_a_description_names_nothing(self, select_task):
        assert select_task.explicit_target("the urgent ones") is None


class TestSelectTaskMatchTarget:
    def test_matches_on_the_issue_in_the_description(self, select_task):
        story = make_story("pending", description="Resolve issue #613: a thing")
        found, error = select_task.match_target([story], ("ref", 613))
        assert (found, error) == (story, None)

    def test_matches_on_the_pr_number(self, select_task):
        story = make_story("pushed", prNumber=617)
        found, error = select_task.match_target([story], ("ref", 617))
        assert (found, error) == (story, None)

    def test_refuses_a_skipped_story_with_the_reason_it_was_skipped(self, select_task):
        """The case that sent an iteration to the wrong story. The skip holds,
        because its reason is the answer the request was really after — here a
        blocker and the condition that clears it."""
        story = make_story(
            "skipped",
            description="Resolve issue #613: a thing",
            skipReason="Blocked on PR #590. Requeue once #590 is merged.",
        )
        found, error = select_task.match_target([story], ("ref", 613))
        assert found is None
        assert "US-001 is skipped" in error
        assert "Requeue once #590 is merged" in error

    def test_refuses_an_invalid_story_the_same_way(self, select_task):
        story = make_story(
            "invalid", description="Resolve issue #613: a thing", skipReason="duplicate"
        )
        found, error = select_task.match_target([story], ("ref", 613))
        assert found is None and "US-001 is invalid: duplicate" in error

    def test_a_skip_with_no_reason_recorded_still_says_so(self, select_task):
        story = make_story("skipped", description="Resolve issue #613: a thing")
        found, error = select_task.match_target([story], ("ref", 613))
        assert found is None and "no reason recorded" in error

    def test_refuses_a_merged_story(self, select_task):
        story = make_story("merged", description="Resolve issue #613: a thing")
        found, error = select_task.match_target([story], ("ref", 613))
        assert found is None and "already merged" in error

    def test_refuses_what_no_story_works(self, select_task):
        story = make_story("pending", description="Resolve issue #101: a thing")
        found, error = select_task.match_target([story], ("ref", 613))
        assert found is None and "no story in the PRD works it" in error

    def test_refuses_a_story_another_run_holds(self, select_task):
        story = make_story("pending", description="Resolve issue #613: a thing")
        found, error = select_task.match_target(
            [story], ("ref", 613), claimed={"US-001"}
        )
        assert found is None and "another run holds US-001" in error

    def test_refuses_an_ambiguous_number(self, select_task):
        stories = [
            make_story("pending", id="US-001", description="Resolve issue #613: one"),
            make_story("pushed", id="US-002", prNumber=613),
        ]
        found, error = select_task.match_target(stories, ("ref", 613))
        assert found is None and "US-001, US-002" in error


class TestSelectTaskNamedStoryIsNotSubstituted:
    """A story named outright is worked or nothing is.

    Selecting a different story burns the iteration on work nobody asked for,
    and the JSON says `selected` either way — so the run looks like it honoured
    the request. These drive the real selection path, including its output.
    """

    def _args(self, tmp_dir, stories, extra_prompt, checked=()):
        os.makedirs(os.path.join(tmp_dir, "data"), exist_ok=True)
        prd_path = os.path.join(tmp_dir, "data", "prd.json")
        with open(prd_path, "w") as f:
            json.dump({"stories": stories}, f)
        run_state_path = os.path.join(tmp_dir, "data", "run-state.json")
        with open(run_state_path, "w") as f:
            json.dump({"runId": "test", "storiesCheckedThisRun": list(checked)}, f)
        return Namespace(
            prd=prd_path,
            run_state=run_state_path,
            iteration_log=None,
            extra_prompt=extra_prompt,
            # Nothing on this path may reach the model, so a binary that does
            # not exist stands in for one: llm_select treats it as no answer.
            claude_bin=os.path.join(tmp_dir, "no-such-claude"),
            slot=1,
            run_pid=os.getpid(),
            run_id="test",
        )

    def _select(self, select_task, tmp_dir, stories, extra_prompt, capsys, checked=()):
        args = self._args(tmp_dir, stories, extra_prompt, checked)
        code = select_task._select_locked(
            args, args.prd, args.run_state, tmp_dir, in_progress=set()
        )
        return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    def _backlog(self):
        return [
            make_story(
                "skipped",
                id="US-212",
                priority=212,
                description="issue #613",
                skipReason="Blocked on PR #590.",
            ),
            make_story("pending", id="US-217", priority=217, description="issue #620"),
        ]

    def test_a_named_skipped_story_reports_its_skip(self, select_task, tmp_dir, capsys):
        """The run this came from: naming #613 worked #620 for an hour."""
        code, out = self._select(
            select_task,
            tmp_dir,
            self._backlog(),
            "https://github.com/brave/bravebot/issues/613",
            capsys,
        )
        assert code == 2
        assert "US-212 is skipped: Blocked on PR #590." in out["reason"]
        assert "US-217" not in json.dumps(out), "picked a story nobody asked for"

    def test_a_named_story_already_worked_this_run_is_still_selected(
        self, select_task, tmp_dir, capsys
    ):
        """Naming a story reaches past the filters the ordinary queue applies —
        the reason matching runs over the whole PRD and not the candidates."""
        code, out = self._select(
            select_task,
            tmp_dir,
            self._backlog(),
            "US-217",
            capsys,
            checked=["US-217"],
        )
        assert code == 0
        assert out["storyId"] == "US-217", out

    def test_an_unmatched_issue_does_not_fall_back(self, select_task, tmp_dir, capsys):
        code, out = self._select(
            select_task,
            tmp_dir,
            [self._backlog()[1]],
            "https://github.com/brave/bravebot/issues/613",
            capsys,
        )
        assert code == 2
        assert out["selected"] is False
        assert "#613" in out["reason"]
        assert "US-217" not in json.dumps(out), "picked a story nobody asked for"

    def test_a_refusal_claims_nothing(self, select_task, tmp_dir, capsys):
        """Nothing may be left holding a story the run never took."""
        self._select(select_task, tmp_dir, [self._backlog()[1]], "#613", capsys)
        claims = os.path.join(tmp_dir, "data", "claims.json")
        assert not os.path.exists(claims) or json.load(open(claims)) == {}

    def test_a_hint_that_matches_nothing_still_falls_back(
        self, select_task, tmp_dir, capsys
    ):
        """The control. A request that names no story is a preference, so an
        unusable answer from the model leaves the deterministic queue in
        charge rather than stopping the run."""
        code, out = self._select(
            select_task, tmp_dir, self._backlog(), "something small", capsys
        )
        assert code == 0
        assert out["storyId"] == "US-217", out


# ═══════════════════════════════════════════════════════════════════════════
# business-hours-elapsed.py
# ═══════════════════════════════════════════════════════════════════════════


class TestBusinessHours:
    def test_same_time_is_zero(self, business_hours):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert business_hours.business_hours_between(now, now) == 0.0

    def test_ref_after_now_is_zero(self, business_hours):
        ref = datetime(2026, 1, 16, 12, 0, tzinfo=timezone.utc)
        now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert business_hours.business_hours_between(ref, now) == 0.0

    def test_full_weekday(self, business_hours):
        ref = datetime(2026, 1, 14, 0, 0, tzinfo=timezone.utc)  # Wednesday
        now = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)  # Thursday
        assert business_hours.business_hours_between(ref, now) == 24.0

    def test_weekend_excluded(self, business_hours):
        ref = datetime(2026, 1, 16, 0, 0, tzinfo=timezone.utc)  # Friday
        now = datetime(2026, 1, 19, 0, 0, tzinfo=timezone.utc)  # Monday
        assert business_hours.business_hours_between(ref, now) == 24.0

    def test_saturday_to_sunday_is_zero(self, business_hours):
        ref = datetime(2026, 1, 17, 8, 0, tzinfo=timezone.utc)  # Saturday
        now = datetime(2026, 1, 18, 20, 0, tzinfo=timezone.utc)  # Sunday
        assert business_hours.business_hours_between(ref, now) == 0.0

    def test_full_work_week(self, business_hours):
        ref = datetime(2026, 1, 12, 0, 0, tzinfo=timezone.utc)  # Monday
        now = datetime(2026, 1, 17, 0, 0, tzinfo=timezone.utc)  # Saturday
        assert business_hours.business_hours_between(ref, now) == 120.0

    def test_across_weekend(self, business_hours):
        ref = datetime(2026, 1, 16, 12, 0, tzinfo=timezone.utc)  # Friday noon
        now = datetime(2026, 1, 19, 12, 0, tzinfo=timezone.utc)  # Monday noon
        assert business_hours.business_hours_between(ref, now) == 24.0

    def test_half_hour_precision(self, business_hours):
        ref = datetime(2026, 1, 14, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 1, 14, 10, 30, tzinfo=timezone.utc)
        assert business_hours.business_hours_between(ref, now) == 0.5


# ═══════════════════════════════════════════════════════════════════════════
# check-prd-has-work.py
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckPrdHasWork:
    TERMINAL = {"merged", "skipped", "invalid"}

    def _active(self, stories):
        return [s for s in stories if s.get("status", "pending") not in self.TERMINAL]

    def test_all_terminal_returns_empty(self):
        stories = [{"status": "merged"}, {"status": "skipped"}, {"status": "invalid"}]
        assert self._active(stories) == []

    def test_pending_is_active(self):
        stories = [{"id": "US-001", "status": "pending"}, {"status": "merged"}]
        assert len(self._active(stories)) == 1

    def test_pushed_and_committed_are_active(self):
        stories = [{"status": "pushed"}, {"status": "committed"}, {"status": "skipped"}]
        assert len(self._active(stories)) == 2

    def test_missing_status_defaults_to_pending(self):
        assert len(self._active([{"id": "US-001"}])) == 1

    def test_empty_stories(self):
        assert self._active([]) == []


# ═══════════════════════════════════════════════════════════════════════════
# sync-bot-prs-to-prd.py
# ═══════════════════════════════════════════════════════════════════════════


def make_pr(
    number=38869,
    title="Add TI-042",
    files=("docs/best-practices/x.md",),
    body="",
    branch="docs/ti-042",
    draft=False,
):
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/brave/brave-core/pull/{number}",
        "headRefName": branch,
        "isDraft": draft,
        "body": body,
        "files": [{"path": p} for p in files],
    }


class TestSyncBotPrsTracking:
    def test_pr_number_field_is_tracked(self, sync_bot_prs):
        prd = {"stories": [make_story(prNumber=38540)]}
        assert 38540 in sync_bot_prs.tracked_pr_numbers(prd)

    def test_pr_url_is_tracked(self, sync_bot_prs):
        prd = {
            "stories": [
                make_story(prUrl="https://github.com/brave/brave-core/pull/38603")
            ]
        }
        assert 38603 in sync_bot_prs.tracked_pr_numbers(prd)

    def test_description_pr_reference_is_tracked(self, sync_bot_prs):
        prd = {"stories": [make_story(description="Land PR #38869 in brave-core.")]}
        assert 38869 in sync_bot_prs.tracked_pr_numbers(prd)

    def test_archived_prd_counts_as_tracked(self, sync_bot_prs):
        prd = {"stories": []}
        archived = {"stories": [make_story(prNumber=37286)]}
        assert 37286 in sync_bot_prs.tracked_pr_numbers(prd, archived)

    def test_missing_archived_prd_is_tolerated(self, sync_bot_prs):
        assert sync_bot_prs.tracked_pr_numbers({"stories": []}, None) == set()


class TestSyncBotPrsLinkedIssue:
    def test_closing_keyword_with_issue_repo(self, sync_bot_prs):
        pr = make_pr(body=f"Fixes {sync_bot_prs._issue_repo}#57147")
        assert sync_bot_prs.linked_issue_number(pr) == 57147

    def test_closing_keyword_with_issue_url(self, sync_bot_prs):
        pr = make_pr(
            body=f"Resolves https://github.com/{sync_bot_prs._issue_repo}/issues/57147"
        )
        assert sync_bot_prs.linked_issue_number(pr) == 57147

    def test_bare_hash_refers_to_pr_repo_not_issue_repo(self, sync_bot_prs):
        # "#38724" in a brave-core PR body is a brave-core PR, not an issue.
        pr = make_pr(body="Learned from review feedback on #38724")
        assert sync_bot_prs.linked_issue_number(pr) is None

    def test_no_closing_keyword(self, sync_bot_prs):
        pr = make_pr(body=f"See {sync_bot_prs._issue_repo}#57147 for background")
        assert sync_bot_prs.linked_issue_number(pr) is None

    def test_empty_body(self, sync_bot_prs):
        assert sync_bot_prs.linked_issue_number(make_pr(body=None)) is None


class TestSyncBotPrsDocsOnly:
    def test_markdown_only_is_docs_only(self, sync_bot_prs):
        pr = make_pr(files=("docs/a.md", "docs/b.md"))
        assert sync_bot_prs.is_docs_only(pr) is True

    def test_mixed_files_is_not_docs_only(self, sync_bot_prs):
        pr = make_pr(files=("docs/a.md", "brave/browser/x.cc"))
        assert sync_bot_prs.is_docs_only(pr) is False

    def test_no_file_data_is_not_docs_only(self, sync_bot_prs):
        assert sync_bot_prs.is_docs_only(make_pr(files=())) is False


class TestSyncBotPrsStory:
    @staticmethod
    def _validations(sync_bot_prs):
        """The checks the configured profile says a code change must pass.

        Asserted against rather than hard-coded strings: which checks exist is
        the project's answer, and this script must not have its own.
        """
        sys.path.insert(0, SCRIPT_DIR)
        from lib.load_config import build_validations, test_step

        profile = sync_bot_prs._profile
        steps = build_validations(profile, test_step(profile, "generic"))
        assert steps, "the configured profile defines no validations"
        return steps

    def test_docs_only_story_omits_build_criteria(self, sync_bot_prs):
        story = sync_bot_prs.build_pr_story(333, 332, make_pr())
        criteria = story["acceptanceCriteria"]
        for step in self._validations(sync_bot_prs):
            assert step not in criteria
        assert story["docsOnly"] is True

    def test_code_story_includes_build_criteria(self, sync_bot_prs):
        story = sync_bot_prs.build_pr_story(
            333, 332, make_pr(files=("brave/browser/x.cc",))
        )
        criteria = story["acceptanceCriteria"]
        for step in self._validations(sync_bot_prs):
            assert step in criteria
        assert story["docsOnly"] is False

    def test_story_is_pushed_with_pr_fields(self, sync_bot_prs):
        story = sync_bot_prs.build_pr_story(333, 332, make_pr())
        assert story["id"] == "US-333"
        assert story["status"] == "pushed"
        assert story["prNumber"] == 38869
        assert story["branchName"] == "docs/ti-042"
        assert story["sourcedFromPr"] is True

    def test_seeded_activity_is_bot_not_reviewer(self, sync_bot_prs):
        # "reviewer" would fake TIER_URGENT before any review data is read.
        assert (
            sync_bot_prs.build_pr_story(333, 332, make_pr())["lastActivityBy"] == "bot"
        )

    def test_linked_issue_uses_dedupe_phrase(self, sync_bot_prs):
        pr = make_pr(body=f"Closes {sync_bot_prs._issue_repo}#57147")
        story = sync_bot_prs.build_pr_story(333, 332, pr)
        # add-backlog-to-prd dedupes on this exact phrase.
        assert "issue #57147" in story["description"]


class TestSyncBotPrsMain:
    def _run(self, sync_bot_prs, monkeypatch, prd_path, prs, extra_args=()):
        monkeypatch.setattr(
            sync_bot_prs, "fetch_bot_prs", lambda pr_number=None, state="open": prs
        )
        argv = [
            "sync-bot-prs-to-prd.py",
            "--prd",
            prd_path,
            "--archived-prd",
            prd_path + ".missing",
            *extra_args,
        ]
        monkeypatch.setattr(sys, "argv", argv)
        return sync_bot_prs.main()

    def test_adds_untracked_pr(self, sync_bot_prs, monkeypatch, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": [make_story(priority=5)]})
        assert self._run(sync_bot_prs, monkeypatch, prd_path, [make_pr()]) == 0
        stories = read_json(prd_path)["stories"]
        assert len(stories) == 2
        assert stories[1]["prNumber"] == 38869
        assert stories[1]["priority"] == 6

    def test_skips_tracked_pr(self, sync_bot_prs, monkeypatch, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": [make_story(prNumber=38869)]})
        assert self._run(sync_bot_prs, monkeypatch, prd_path, [make_pr()]) == 0
        assert len(read_json(prd_path)["stories"]) == 1

    def test_skips_draft_pr(self, sync_bot_prs, monkeypatch, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": []})
        assert (
            self._run(sync_bot_prs, monkeypatch, prd_path, [make_pr(draft=True)]) == 0
        )
        assert read_json(prd_path)["stories"] == []

    def test_dry_run_writes_nothing(
        self, sync_bot_prs, monkeypatch, write_json, read_json
    ):
        prd_path = write_json("prd.json", {"stories": []})
        assert (
            self._run(sync_bot_prs, monkeypatch, prd_path, [make_pr()], ["--dry-run"])
            == 0
        )
        assert read_json(prd_path)["stories"] == []

    def test_existing_stories_untouched(
        self, sync_bot_prs, monkeypatch, write_json, read_json
    ):
        existing = make_story(id="US-001", priority=5, status="merged")
        prd_path = write_json("prd.json", {"stories": [existing]})
        self._run(sync_bot_prs, monkeypatch, prd_path, [make_pr()])
        assert read_json(prd_path)["stories"][0] == existing

    def test_ids_continue_from_highest_existing(
        self, sync_bot_prs, monkeypatch, write_json, read_json
    ):
        prd_path = write_json(
            "prd.json", {"stories": [make_story(id="US-330", priority=1027)]}
        )
        self._run(sync_bot_prs, monkeypatch, prd_path, [make_pr()])
        assert read_json(prd_path)["stories"][1]["id"] == "US-331"


class TestSelectTaskCandidateSummary:
    def test_includes_pr_number(self, select_task):
        line = select_task.candidate_summary([make_story(prNumber=38869)])
        assert "PR #38869" in line

    def test_includes_issue_number_from_description(self, select_task):
        story = make_story(description="Fix the failure in Foo.Bar (issue #56971).")
        assert "issue #56971" in select_task.candidate_summary([story])

    def test_omits_refs_when_absent(self, select_task):
        line = select_task.candidate_summary([make_story(id="US-001", priority=1)])
        assert line == '- US-001: "Story US-001" (status: pending, priority: 1)'

    def test_one_line_per_candidate(self, select_task):
        stories = [make_story(id="US-001"), make_story(id="US-002")]
        assert len(select_task.candidate_summary(stories).splitlines()) == 2

    def test_includes_the_triage_axes(self, select_task):
        """A request phrased as an axis ("the urgent ones") needs something to
        match on, and axis order is fixed so two stories read the same way."""
        story = make_story(triage={"size": 2, "urgency": 3, "importance": 2})
        line = select_task.candidate_summary([story])
        assert "importance 2, urgency 3, size 2" in line


# ── scripts/lib/git-identity.sh ──────────────────────────────────────────────

GIT_IDENTITY_LIB = os.path.join(SCRIPT_DIR, "lib", "git-identity.sh")


def run_identity_snippet(snippet, home=None, env=None):
    """Source git-identity.sh and run a bash snippet against it."""
    full_env = dict(os.environ)
    if home is not None:
        full_env["HOME"] = str(home)
    if env:
        full_env.update(env)
    # /bin/bash, not PATH bash: the scripts run under macOS's bash 3.2, so
    # bash 4+ builtins must fail here rather than in production.
    return subprocess.run(
        ["/bin/bash", "-c", f'source "{GIT_IDENTITY_LIB}"\n{snippet}'],
        capture_output=True,
        text=True,
        env=full_env,
    )


class TestBotSshCommand:
    def test_includes_identities_only(self):
        # Without IdentitiesOnly, ssh-agent offers the machine owner's key and
        # GitHub authenticates as them regardless of -i.
        out = run_identity_snippet("bot_ssh_command /home/bot/.ssh/k").stdout
        assert "-o IdentitiesOnly=yes" in out
        assert "-i /home/bot/.ssh/k" in out

    def test_quotes_paths_with_spaces(self):
        # git runs core.sshCommand through a shell, so the path must survive it.
        out = run_identity_snippet("bot_ssh_command '/home/my bot/.ssh/k'").stdout
        probe = subprocess.run(
            ["bash", "-c", f'set -- {out}; echo "${{@: -1}}"'],
            capture_output=True,
            text=True,
        )
        assert probe.stdout.strip() == "/home/my bot/.ssh/k"

    def test_fails_on_empty_key(self):
        assert run_identity_snippet("bot_ssh_command ''").returncode != 0


class TestBotSshLogin:
    def _fake_ssh(self, tmp_path, greeting, *, slurp_stdin=True):
        """Stub ssh. Real GitHub exits non-zero even on success, so does this.

        The stub drains stdin by default, which is what real `ssh -T` does and
        what the </dev/null guard in bot_ssh_login exists to prevent.
        """
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        drain = "cat >/dev/null" if slurp_stdin else "true"
        (bindir / "ssh").write_text(
            f"#!/bin/bash\n{drain}\necho {greeting!r}\nexit 1\n"
        )
        (bindir / "ssh").chmod(0o755)
        return {"PATH": f"{bindir}:{os.environ['PATH']}"}

    def test_parses_login_from_greeting(self, tmp_path):
        env = self._fake_ssh(
            tmp_path,
            "Hi widgetbot! You've successfully authenticated, but GitHub does "
            "not provide shell access.",
        )
        result = run_identity_snippet("bot_ssh_login /keys/bot", env=env)
        assert result.stdout == "widgetbot"

    def test_rejected_key_yields_empty(self, tmp_path):
        env = self._fake_ssh(tmp_path, "git@github.com: Permission denied (publickey).")
        result = run_identity_snippet("bot_ssh_login /keys/bot", env=env)
        assert result.stdout == ""

    def test_rejected_key_does_not_trip_set_e(self, tmp_path):
        # The caller runs under `set -e`. A key GitHub rejects is an expected
        # outcome reported via empty stdout, not a fatal error.
        env = self._fake_ssh(tmp_path, "git@github.com: Permission denied (publickey).")
        result = run_identity_snippet(
            'set -e\nlogin=$(bot_ssh_login /keys/bot)\necho "reached:[$login]"',
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert "reached:[]" in result.stdout

    def test_does_not_consume_caller_stdin(self, tmp_path):
        # `ssh -T` slurps stdin, which would eat the setup wizard's remaining
        # answers when input is piped rather than typed.
        env = self._fake_ssh(tmp_path, "Hi widgetbot!")
        full_env = dict(os.environ)
        full_env.update(env)
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                f'source "{GIT_IDENTITY_LIB}"\n'
                "bot_ssh_login /keys/bot >/dev/null\n"
                'read -r answer\necho "answer=$answer"',
            ],
            input="still-here\n",
            capture_output=True,
            text=True,
            env=full_env,
        )
        assert "answer=still-here" in result.stdout


class TestBotListSshKeys:
    def _fake_home(self, tmp_path, files):
        ssh = tmp_path / ".ssh"
        ssh.mkdir()
        for name, content in files.items():
            (ssh / name).write_text(content)
        return tmp_path

    def test_sniffs_content_not_extension(self, tmp_path):
        # A PEM key named *.ppk is still a usable key; id_* globbing misses it.
        home = self._fake_home(
            tmp_path,
            {
                "github.ppk": "-----BEGIN RSA PRIVATE KEY-----\nx\n",
                "id_ed25519": "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n",
            },
        )
        out = run_identity_snippet("bot_list_ssh_keys", home=home).stdout
        assert sorted(os.path.basename(line) for line in out.split()) == [
            "github.ppk",
            "id_ed25519",
        ]

    def test_prefers_public_key_over_its_private_half(self, tmp_path):
        # ssh matches a .pub against ssh-agent and falls back to the private
        # file beside it, so the .pub is the strictly more capable -i target.
        home = self._fake_home(
            tmp_path,
            {
                "id_rsa": "-----BEGIN RSA PRIVATE KEY-----\nx\n",
                "id_rsa.pub": "ssh-rsa AAAA...\n",
            },
        )
        out = run_identity_snippet("bot_list_ssh_keys", home=home).stdout
        assert [os.path.basename(line) for line in out.split()] == ["id_rsa.pub"]

    def test_lists_public_key_whose_private_half_is_named_differently(self, tmp_path):
        # The real case this was written for: an encrypted PEM key held in
        # ssh-agent, whose public half is github.pub rather than github.ppk.pub.
        # -i github.ppk cannot reach the agent copy; -i github.pub can.
        home = self._fake_home(
            tmp_path,
            {
                "github.ppk": "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n",
                "github.pub": "ssh-rsa AAAA...\n",
            },
        )
        out = run_identity_snippet("bot_list_ssh_keys", home=home).stdout
        assert sorted(os.path.basename(line) for line in out.split()) == [
            "github.ppk",
            "github.pub",
        ]

    def test_collapses_pair_whose_halves_are_named_differently(self, tmp_path):
        # netzenbot.pub / netzenbot.ppk. The name rule cannot see this pair, so
        # both halves were offered as if they were two keys — and with opposite
        # [needs ssh-agent] markers, since ssh finds no `netzenbot` beside the
        # .pub to fall back to. Matching fingerprints collapse them to the half
        # that still authenticates from cron.
        ssh = tmp_path / ".ssh"
        ssh.mkdir()
        key = ssh / "netzenbot"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
        )
        key.rename(ssh / "netzenbot.ppk")
        out = run_identity_snippet("bot_list_ssh_keys", home=tmp_path).stdout
        listed = out.split()
        assert [os.path.basename(line) for line in listed] == ["netzenbot.ppk"]
        # The surviving entry is the one that does not depend on ssh-agent.
        assert (
            run_identity_snippet(f"bot_identity_needs_agent '{listed[0]}'").returncode
            == 1
        )

    def test_real_name_matched_pair_still_prefers_the_pub(self, tmp_path):
        # Same assertion as above one level up, but with a real key: the
        # fingerprint pass must not undo the name rule for id_rsa / id_rsa.pub.
        ssh = tmp_path / ".ssh"
        ssh.mkdir()
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(ssh / "id_x")],
            check=True,
        )
        out = run_identity_snippet("bot_list_ssh_keys", home=tmp_path).stdout
        assert [os.path.basename(line) for line in out.split()] == ["id_x.pub"]

    def test_excludes_non_keys(self, tmp_path):
        home = self._fake_home(
            tmp_path,
            {
                "id_rsa": "-----BEGIN RSA PRIVATE KEY-----\nx\n",
                "config": "Host github.com\n  IdentityFile ~/.ssh/id_rsa\n",
                "known_hosts": "github.com ssh-ed25519 AAAA...\n",
                "cert.pem": "-----BEGIN CERTIFICATE-----\nx\n",
            },
        )
        out = run_identity_snippet("bot_list_ssh_keys", home=home).stdout
        assert [os.path.basename(line) for line in out.split()] == ["id_rsa"]


class TestBotIdentityNeedsAgent:
    def test_missing_private_half_needs_agent(self, tmp_path):
        pub = tmp_path / "k.pub"
        pub.write_text("ssh-rsa AAAA...\n")
        result = run_identity_snippet(f"bot_identity_needs_agent '{pub}'")
        assert result.returncode == 0

    def test_unencrypted_key_works_without_agent(self, tmp_path):
        key = tmp_path / "k"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
        )
        # Both the private path and its .pub resolve to the same usable key.
        assert run_identity_snippet(f"bot_identity_needs_agent '{key}'").returncode == 1
        assert (
            run_identity_snippet(f"bot_identity_needs_agent '{key}.pub'").returncode
            == 1
        )

    def test_encrypted_key_needs_agent(self, tmp_path):
        key = tmp_path / "k"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "hunter2", "-f", str(key)],
            check=True,
        )
        # Must not hang waiting for a passphrase prompt.
        result = run_identity_snippet(f"bot_identity_needs_agent '{key}'")
        assert result.returncode == 0

    def test_empty_when_no_ssh_dir(self, tmp_path):
        result = run_identity_snippet("bot_list_ssh_keys", home=tmp_path)
        assert result.stdout.strip() == ""
        assert result.returncode == 0


class TestBotApplyRepoIdentity:
    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        return repo

    def test_writes_local_config_only(self, tmp_path):
        repo = self._repo(tmp_path)
        # Redirect global config at a throwaway file so the assertion below
        # proves nothing leaked, rather than reading the developer's real one.
        fake_global = tmp_path / "gitconfig"
        fake_global.write_text("")
        env = {"GIT_CONFIG_GLOBAL": str(fake_global)}
        run_identity_snippet(
            f"bot_apply_repo_identity '{repo}' bot bot@example.com /keys/bot", env=env
        )
        local = (repo / ".git" / "config").read_text()
        assert "bot@example.com" in local
        assert "IdentitiesOnly=yes" in local
        # The point of the whole exercise: no global state touched.
        assert fake_global.read_text() == ""

    def test_unsets_ssh_command_when_key_cleared(self, tmp_path):
        repo = self._repo(tmp_path)
        run_identity_snippet(f"bot_apply_repo_identity '{repo}' bot b@e.com /keys/bot")
        run_identity_snippet(f"bot_apply_repo_identity '{repo}' bot b@e.com ''")
        result = subprocess.run(
            ["git", "-C", str(repo), "config", "--local", "--get", "core.sshCommand"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == ""

    def test_succeeds_with_no_key_configured(self, tmp_path):
        repo = self._repo(tmp_path)
        result = run_identity_snippet(
            f"set -e\nbot_apply_repo_identity '{repo}' bot b@e.com ''"
        )
        assert result.returncode == 0, result.stderr


class TestSignaturesVerifyLocally:
    """Whether git can tell the bot's signed commit from an unsigned one.

    Signing and verifying are separately configured, and without the second git reports a good
    signature as "No signature": the same words it uses for a commit that never had one.
    """

    def _signing_repo(self, tmp_path):
        """A repo configured as the bot signs, with the key beside its own public half.

        ssh-keygen falls back to the private file next to the .pub it was given, so nothing here
        needs an agent. Global config is redirected at a throwaway file because this machine's own
        signing settings would otherwise decide what these tests observe.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        key = tmp_path / "botkey"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "bot",
                "-f",
                str(key),
            ],
            check=True,
        )
        fake_global = tmp_path / "gitconfig"
        fake_global.write_text("")
        env = {"GIT_CONFIG_GLOBAL": str(fake_global), "GIT_CONFIG_NOSYSTEM": "1"}
        run_identity_snippet(
            f"bot_apply_repo_identity '{repo}' bot bot@example.com '{key}.pub'",
            env=env,
        )
        return repo, dict(os.environ, **env)

    def _commit(self, repo, env):
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "signed"],
            check=True,
            env=env,
        )

    def _verdict(self, repo, env, cwd=None):
        return subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%G?"],
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
        )

    def test_a_signed_commit_reads_as_signed(self, tmp_path):
        repo, env = self._signing_repo(tmp_path)
        self._commit(repo, env)
        result = self._verdict(repo, env)
        assert result.stdout.strip() == "G", result.stderr

    def test_it_holds_below_the_top_of_the_repo(self, tmp_path):
        # A relative allowedSignersFile is resolved against the directory git was run from, so
        # verification would work at the top of the repo and fail in any subdirectory, which is
        # where a hook and an editor both tend to run.
        repo, env = self._signing_repo(tmp_path)
        self._commit(repo, env)
        below = repo / "a" / "b"
        below.mkdir(parents=True)
        result = self._verdict(repo, env, cwd=below)
        assert result.stdout.strip() == "G", result.stderr


class TestBotExportIdentityEnv:
    def _fake_gh(self, tmp_path, tokens):
        """A stub gh that knows tokens for the given accounts."""
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        cases = "\n".join(
            f'    {acct}) echo "{tok}" ;;' for acct, tok in tokens.items()
        )
        (bindir / "gh").write_text(
            "#!/bin/bash\n"
            'if [ "$1 $2" = "auth token" ]; then\n'
            '  case "$4" in\n'
            f"{cases}\n"
            "    *) exit 1 ;;\n"
            "  esac\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n"
        )
        (bindir / "gh").chmod(0o755)
        return bindir

    def test_exports_ssh_command_and_token(self, tmp_path):
        key = tmp_path / "botkey.pub"
        key.write_text("ssh-ed25519 AAAA...\n")
        bindir = self._fake_gh(tmp_path, {"widgetbot": "gho_bot"})
        result = run_identity_snippet(
            f"bot_export_identity_env '{key}' widgetbot\n"
            'echo "ssh=$GIT_SSH_COMMAND"\necho "tok=$GH_TOKEN"',
            env={"PATH": f"{bindir}:{os.environ['PATH']}", "GH_TOKEN": ""},
        )
        assert f"ssh=ssh -o IdentitiesOnly=yes -i {key}" in result.stdout
        assert "tok=gho_bot" in result.stdout

    def test_existing_gh_token_wins(self, tmp_path):
        bindir = self._fake_gh(tmp_path, {"widgetbot": "gho_bot"})
        result = run_identity_snippet(
            "bot_export_identity_env '' widgetbot\necho \"tok=$GH_TOKEN\"",
            env={"PATH": f"{bindir}:{os.environ['PATH']}", "GH_TOKEN": "gho_ci"},
        )
        assert "tok=gho_ci" in result.stdout

    def test_unreadable_key_fails_loudly(self, tmp_path):
        result = run_identity_snippet(
            "bot_export_identity_env '/nope/missing.pub' ''", env={"GH_TOKEN": ""}
        )
        assert result.returncode == 1
        assert "not readable" in result.stderr

    def test_missing_gh_account_warns_but_continues(self, tmp_path):
        key = tmp_path / "botkey.pub"
        key.write_text("ssh-ed25519 AAAA...\n")
        bindir = self._fake_gh(tmp_path, {"someone-else": "gho_other"})
        result = run_identity_snippet(
            f"bot_export_identity_env '{key}' widgetbot\necho \"tok=[$GH_TOKEN]\"",
            env={"PATH": f"{bindir}:{os.environ['PATH']}", "GH_TOKEN": ""},
        )
        # A missing bot token is degraded, not fatal — the run still proceeds.
        assert result.returncode == 0
        assert "no stored token for 'widgetbot'" in result.stderr
        assert "tok=[]" in result.stdout

    def test_no_key_configured_leaves_ssh_alone(self, tmp_path):
        bindir = self._fake_gh(tmp_path, {})
        # A sentinel rather than an empty value: the snippet inherits the
        # caller's environment, and this repo's own .envrc exports
        # GIT_SSH_COMMAND, so asserting emptiness would only pass for
        # developers who don't use direnv here. Untouched is the real claim.
        result = run_identity_snippet(
            "set -e\nbot_export_identity_env '' ''\necho \"ssh=[$GIT_SSH_COMMAND]\"",
            env={
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "GH_TOKEN": "",
                "GIT_SSH_COMMAND": "ssh -i /preexisting/key",
            },
        )
        assert result.returncode == 0
        assert "ssh=[ssh -i /preexisting/key]" in result.stdout


# ═══════════════════════════════════════════════════════════════════════════
# sync-merged-prs-to-prd.py
# ═══════════════════════════════════════════════════════════════════════════

MAIN_WORKTREE = "/checkout/bravebot"
STORY_WORKTREE = "/checkout/bravebot-196"


def porcelain(*entries):
    """A `git worktree list --porcelain` listing for (path, branch) pairs."""
    return "".join(
        f"worktree {path}\nHEAD {'0' * 40}\nbranch refs/heads/{branch}\n\n"
        for path, branch in entries
    )


class TestSyncMergedCandidates:
    def test_only_pushed_stories_are_asked_about(self, sync_merged_prs):
        prd = {
            "stories": [
                make_story(status="pushed", id="US-001", prNumber=11),
                make_story(status="pending", id="US-002", prNumber=22),
                make_story(status="merged", id="US-003", prNumber=33),
                make_story(status="committed", id="US-004", prNumber=44),
            ]
        }
        assert sync_merged_prs.pushed_pr_numbers(prd) == [11]

    def test_a_pushed_story_with_no_pr_number_is_skipped(self, sync_merged_prs):
        prd = {"stories": [make_story(status="pushed", prNumber=None)]}
        assert sync_merged_prs.pushed_pr_numbers(prd) == []

    def test_one_pr_shared_by_two_stories_is_asked_about_once(self, sync_merged_prs):
        prd = {
            "stories": [
                make_story(status="pushed", id="US-001", prNumber=11),
                make_story(status="pushed", id="US-002", prNumber=11),
            ]
        }
        assert sync_merged_prs.pushed_pr_numbers(prd) == [11]


class TestSyncMergedRetire:
    def test_it_writes_the_fields_an_iteration_writes(
        self, sync_merged_prs, update_prd_status
    ):
        # A story retired here has to be indistinguishable from one an agent
        # retired, or the archive reads a half-populated story.
        by_script = make_story(status="pushed", prNumber=11)
        sync_merged_prs.retire(by_script, "2026-09-11T15:10:11Z")
        by_agent = make_story(status="pushed", prNumber=11)
        update_prd_status.handle_merged(by_agent, Namespace())
        assert set(by_script) == set(by_agent)
        assert by_script["status"] == "merged"

    def test_merged_at_is_the_real_merge_time(self, sync_merged_prs):
        story = make_story(status="pushed")
        sync_merged_prs.retire(story, "2026-09-11T15:10:11Z")
        assert story["mergedAt"] == "2026-09-11T15:10:11Z"

    def test_a_pr_with_no_merge_time_still_retires(self, sync_merged_prs):
        story = make_story(status="pushed")
        sync_merged_prs.retire(story, None)
        assert story["status"] == "merged"
        assert story["mergedAt"]


class TestSyncMergedWorktreeLookup:
    def test_a_worktree_is_found_by_the_branch_it_has_checked_out(
        self, sync_merged_prs, monkeypatch
    ):
        listing = porcelain(
            (MAIN_WORKTREE, "main"), (STORY_WORKTREE, "fix-routing-fields")
        )
        monkeypatch.setattr(sync_merged_prs, "run_git", lambda args: listing)
        found = sync_merged_prs.story_worktrees(MAIN_WORKTREE, {"fix-routing-fields"})
        assert found == {"fix-routing-fields": STORY_WORKTREE}

    def test_the_main_checkout_is_never_a_candidate(self, sync_merged_prs, monkeypatch):
        # A project without worktrees leaves the story branch checked out in the
        # main tree. Removing that would take the whole checkout with it.
        listing = porcelain((MAIN_WORKTREE, "fix-routing-fields"))
        monkeypatch.setattr(sync_merged_prs, "run_git", lambda args: listing)
        assert (
            sync_merged_prs.story_worktrees(MAIN_WORKTREE, {"fix-routing-fields"}) == {}
        )

    def test_another_story_s_worktree_is_left_alone(self, sync_merged_prs, monkeypatch):
        listing = porcelain(
            (MAIN_WORKTREE, "main"),
            (STORY_WORKTREE, "fix-routing-fields"),
            ("/checkout/bravebot-197", "fix-something-else"),
        )
        monkeypatch.setattr(sync_merged_prs, "run_git", lambda args: listing)
        found = sync_merged_prs.story_worktrees(MAIN_WORKTREE, {"fix-routing-fields"})
        assert found == {"fix-routing-fields": STORY_WORKTREE}

    def test_an_unreadable_repository_yields_nothing(
        self, sync_merged_prs, monkeypatch
    ):
        monkeypatch.setattr(sync_merged_prs, "run_git", lambda args: None)
        assert sync_merged_prs.story_worktrees(MAIN_WORKTREE, {"fix-x"}) == {}


class TestSyncMergedWorktreeIsDisposable:
    @staticmethod
    def _git(status="", head="deadbeef"):
        def fake(args):
            if "status" in args:
                return status
            if "rev-parse" in args:
                return head + "\n"
            return None

        return fake

    def test_clean_at_the_merged_commit(self, sync_merged_prs, monkeypatch):
        monkeypatch.setattr(sync_merged_prs, "run_git", self._git())
        assert sync_merged_prs.worktree_is_disposable(STORY_WORKTREE, "deadbeef")

    def test_uncommitted_changes_keep_it(self, sync_merged_prs, monkeypatch):
        monkeypatch.setattr(
            sync_merged_prs, "run_git", self._git(status=" M src/main.rs\n")
        )
        assert not sync_merged_prs.worktree_is_disposable(STORY_WORKTREE, "deadbeef")

    def test_a_head_the_pr_never_carried_keeps_it(self, sync_merged_prs, monkeypatch):
        monkeypatch.setattr(sync_merged_prs, "run_git", self._git(head="c0ffee"))
        assert not sync_merged_prs.worktree_is_disposable(STORY_WORKTREE, "deadbeef")

    def test_a_pr_with_no_head_commit_keeps_it(self, sync_merged_prs, monkeypatch):
        monkeypatch.setattr(sync_merged_prs, "run_git", self._git())
        assert not sync_merged_prs.worktree_is_disposable(STORY_WORKTREE, None)


class TestSyncMergedRun:
    def _run(self, sync_merged_prs, monkeypatch, tmp_path, stories, prs, *extra):
        prd_path = tmp_path / "prd.json"
        prd_path.write_text(json.dumps({"stories": stories}))
        monkeypatch.setattr(sync_merged_prs, "fetch_pr", lambda n: prs.get(n))
        # No target checkout in the test config, so worktree cleanup is a no-op.
        monkeypatch.setattr(
            sys, "argv", ["sync-merged-prs-to-prd.py", "--prd", str(prd_path), *extra]
        )
        assert sync_merged_prs.main() == 0
        return json.loads(prd_path.read_text())["stories"]

    def test_a_merged_pr_retires_its_story(
        self, sync_merged_prs, monkeypatch, tmp_path
    ):
        stories = self._run(
            sync_merged_prs,
            monkeypatch,
            tmp_path,
            [make_story(status="pushed", prNumber=228)],
            {228: {"state": "MERGED", "mergedAt": "2026-09-11T18:00:04Z"}},
        )
        assert stories[0]["status"] == "merged"

    def test_an_open_pr_keeps_its_place_in_the_queue(
        self, sync_merged_prs, monkeypatch, tmp_path
    ):
        stories = self._run(
            sync_merged_prs,
            monkeypatch,
            tmp_path,
            [make_story(status="pushed", prNumber=219)],
            {219: {"state": "OPEN", "mergedAt": None}},
        )
        assert stories[0]["status"] == "pushed"

    def test_a_closed_but_unmerged_pr_is_left_for_an_iteration(
        self, sync_merged_prs, monkeypatch, tmp_path
    ):
        # Abandoned, not landed: deciding between "skipped" and reopening it is
        # a judgement call, so this script does not make it.
        stories = self._run(
            sync_merged_prs,
            monkeypatch,
            tmp_path,
            [make_story(status="pushed", prNumber=137)],
            {137: {"state": "CLOSED", "mergedAt": None}},
        )
        assert stories[0]["status"] == "pushed"

    def test_a_pr_github_cannot_be_asked_about_is_left_alone(
        self, sync_merged_prs, monkeypatch, tmp_path
    ):
        stories = self._run(
            sync_merged_prs,
            monkeypatch,
            tmp_path,
            [make_story(status="pushed", prNumber=999)],
            {},
        )
        assert stories[0]["status"] == "pushed"

    def test_dry_run_writes_nothing(self, sync_merged_prs, monkeypatch, tmp_path):
        stories = self._run(
            sync_merged_prs,
            monkeypatch,
            tmp_path,
            [make_story(status="pushed", prNumber=228)],
            {228: {"state": "MERGED", "mergedAt": "2026-09-11T18:00:04Z"}},
            "--dry-run",
        )
        assert stories[0]["status"] == "pushed"

    def test_one_merged_pr_does_not_disturb_the_other_stories(
        self, sync_merged_prs, monkeypatch, tmp_path
    ):
        stories = self._run(
            sync_merged_prs,
            monkeypatch,
            tmp_path,
            [
                make_story(status="pushed", id="US-001", prNumber=228),
                make_story(status="pending", id="US-002"),
                make_story(status="pushed", id="US-003", prNumber=219),
            ],
            {
                228: {"state": "MERGED", "mergedAt": "2026-09-11T18:00:04Z"},
                219: {"state": "OPEN", "mergedAt": None},
            },
        )
        assert [s["status"] for s in stories] == ["merged", "pending", "pushed"]

    def test_a_missing_prd_is_an_error(self, sync_merged_prs, monkeypatch, tmp_path):
        monkeypatch.setattr(
            sys,
            "argv",
            ["sync-merged-prs-to-prd.py", "--prd", str(tmp_path / "nope.json")],
        )
        assert sync_merged_prs.main() == 2


# ═══════════════════════════════════════════════════════════════════════════
# sync-closed-issues-to-prd.py
# ═══════════════════════════════════════════════════════════════════════════


def story_for_issue(number, **overrides):
    """A story that references an issue the way intake writes it."""
    return make_story(
        description=f"Resolve issue #{number}: something is broken", **overrides
    )


def closed_issue(state_reason="COMPLETED", closed_at="2026-09-16T16:36:00Z"):
    return {"state": "CLOSED", "stateReason": state_reason, "closedAt": closed_at}


class TestSyncClosedFetch:
    """The gh call itself. The rest of these tests stand in for fetch_issue."""

    def _gh(self, sync_closed_issues, monkeypatch, returncode=0, stdout="", stderr=""):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

        monkeypatch.setattr(sync_closed_issues.subprocess, "run", fake_run)
        return calls

    def test_it_asks_graphql_rather_than_for_a_json_field(
        self, sync_closed_issues, monkeypatch
    ):
        # `gh issue view --json stateReason` needs gh >= 2.46, and an unknown
        # field fails the whole read, so every issue in the pass errored out.
        calls = self._gh(
            sync_closed_issues,
            monkeypatch,
            stdout=json.dumps({"data": {"repository": {"issue": closed_issue()}}}),
        )
        assert sync_closed_issues.fetch_issue(116) == closed_issue()
        assert calls[0][:3] == ["gh", "api", "graphql"]
        assert "--json" not in calls[0]

    def test_an_issue_that_does_not_exist_reads_as_unknown(
        self, sync_closed_issues, monkeypatch
    ):
        # GraphQL answers a bad number with a null issue and a non-zero exit;
        # either way the story keeps its place rather than being retired.
        self._gh(
            sync_closed_issues,
            monkeypatch,
            stdout=json.dumps({"data": {"repository": {"issue": None}}}),
        )
        assert sync_closed_issues.fetch_issue(999999) is None

    def test_a_failed_read_is_not_fatal(self, sync_closed_issues, monkeypatch):
        self._gh(sync_closed_issues, monkeypatch, returncode=1, stderr="no auth")
        assert sync_closed_issues.fetch_issue(116) is None

    def test_an_unreadable_response_is_not_fatal(self, sync_closed_issues, monkeypatch):
        self._gh(sync_closed_issues, monkeypatch, stdout="not json")
        assert sync_closed_issues.fetch_issue(116) is None


class TestSyncClosedCandidates:
    def test_only_pending_stories_with_no_pr_are_asked_about(self, sync_closed_issues):
        # A committed or pushed story has commits or a PR that a person has to
        # dispose of; retiring it here would orphan them silently.
        prd = {
            "stories": [
                story_for_issue(116, id="US-001", status="pending"),
                story_for_issue(117, id="US-002", status="pushed", prNumber=22),
                story_for_issue(118, id="US-003", status="committed"),
                story_for_issue(119, id="US-004", status="merged", prNumber=44),
                story_for_issue(120, id="US-005", status="invalid"),
                story_for_issue(121, id="US-006", status="pending", prNumber=66),
            ]
        }
        assert sync_closed_issues.candidate_issue_numbers(prd) == [116]

    def test_a_story_with_no_issue_reference_is_skipped(self, sync_closed_issues):
        prd = {"stories": [make_story(status="pending")]}
        assert sync_closed_issues.candidate_issue_numbers(prd) == []

    def test_one_issue_shared_by_two_stories_is_asked_about_once(
        self, sync_closed_issues
    ):
        prd = {
            "stories": [
                story_for_issue(116, id="US-001"),
                story_for_issue(116, id="US-002"),
            ]
        }
        assert sync_closed_issues.candidate_issue_numbers(prd) == [116]


class TestSyncClosedRetire:
    def test_it_writes_the_fields_an_iteration_writes(
        self, sync_closed_issues, update_prd_status
    ):
        by_script = story_for_issue(116)
        sync_closed_issues.retire(by_script, 116, closed_issue())
        by_agent = story_for_issue(116)
        update_prd_status.handle_invalid(by_agent, Namespace(reason="already fixed"))
        assert set(by_script) == set(by_agent)
        assert by_script["status"] == "invalid"

    def test_the_reason_names_the_issue_and_how_it_was_closed(self, sync_closed_issues):
        story = story_for_issue(116)
        sync_closed_issues.retire(story, 116, closed_issue())
        assert "#116" in story["skipReason"]
        assert "COMPLETED" in story["skipReason"]
        assert "2026-09-16T16:36:00Z" in story["skipReason"]

    def test_the_reason_survives_an_issue_closed_without_a_state_reason(
        self, sync_closed_issues
    ):
        story = story_for_issue(116)
        sync_closed_issues.retire(story, 116, {"state": "CLOSED"})
        assert "CLOSED" in story["skipReason"]


class TestSyncClosedRun:
    def _run(self, sync_closed_issues, monkeypatch, tmp_path, stories, issues, *extra):
        prd_path = tmp_path / "prd.json"
        prd_path.write_text(json.dumps({"stories": stories}))
        monkeypatch.setattr(sync_closed_issues, "fetch_issue", lambda n: issues.get(n))
        monkeypatch.setattr(
            sys,
            "argv",
            ["sync-closed-issues-to-prd.py", "--prd", str(prd_path), *extra],
        )
        assert sync_closed_issues.main() == 0
        return json.loads(prd_path.read_text())["stories"]

    def test_a_closed_issue_retires_its_story(
        self, sync_closed_issues, monkeypatch, tmp_path
    ):
        stories = self._run(
            sync_closed_issues,
            monkeypatch,
            tmp_path,
            [story_for_issue(116)],
            {116: closed_issue()},
        )
        assert stories[0]["status"] == "invalid"

    def test_an_issue_closed_as_not_planned_retires_its_story_too(
        self, sync_closed_issues, monkeypatch, tmp_path
    ):
        # Declined is as final as done: neither is work the loop should open a
        # pull request for.
        stories = self._run(
            sync_closed_issues,
            monkeypatch,
            tmp_path,
            [story_for_issue(116)],
            {116: closed_issue(state_reason="NOT_PLANNED")},
        )
        assert stories[0]["status"] == "invalid"
        assert "NOT_PLANNED" in stories[0]["skipReason"]

    def test_an_open_issue_keeps_its_place_in_the_queue(
        self, sync_closed_issues, monkeypatch, tmp_path
    ):
        stories = self._run(
            sync_closed_issues,
            monkeypatch,
            tmp_path,
            [story_for_issue(116)],
            {116: {"state": "OPEN", "stateReason": None, "closedAt": None}},
        )
        assert stories[0]["status"] == "pending"

    def test_an_issue_github_cannot_be_asked_about_is_left_alone(
        self, sync_closed_issues, monkeypatch, tmp_path
    ):
        stories = self._run(
            sync_closed_issues, monkeypatch, tmp_path, [story_for_issue(116)], {}
        )
        assert stories[0]["status"] == "pending"

    def test_a_pushed_story_is_left_for_an_iteration(
        self, sync_closed_issues, monkeypatch, tmp_path
    ):
        # Its PR is still open against a closed issue: closing or repointing it
        # is a judgement this script does not make.
        stories = self._run(
            sync_closed_issues,
            monkeypatch,
            tmp_path,
            [story_for_issue(116, status="pushed", prNumber=349)],
            {116: closed_issue()},
        )
        assert stories[0]["status"] == "pushed"

    def test_dry_run_writes_nothing(self, sync_closed_issues, monkeypatch, tmp_path):
        stories = self._run(
            sync_closed_issues,
            monkeypatch,
            tmp_path,
            [story_for_issue(116)],
            {116: closed_issue()},
            "--dry-run",
        )
        assert stories[0]["status"] == "pending"

    def test_one_closed_issue_does_not_disturb_the_other_stories(
        self, sync_closed_issues, monkeypatch, tmp_path
    ):
        stories = self._run(
            sync_closed_issues,
            monkeypatch,
            tmp_path,
            [
                story_for_issue(116, id="US-001"),
                story_for_issue(117, id="US-002"),
                make_story(id="US-003"),
            ],
            {
                116: closed_issue(),
                117: {"state": "OPEN", "stateReason": None, "closedAt": None},
            },
        )
        assert [s["status"] for s in stories] == ["invalid", "pending", "pending"]

    def test_a_story_another_run_claimed_mid_pass_is_left_alone(
        self, sync_closed_issues, monkeypatch, tmp_path
    ):
        # The GitHub reads are deliberately outside the PRD lock, so another
        # run's iteration can push this story while its issue is being asked
        # about. Retiring it then would drop the PR that iteration just opened.
        prd_path = tmp_path / "prd.json"
        prd_path.write_text(json.dumps({"stories": [story_for_issue(116)]}))

        def claim_it_meanwhile(number):
            prd_path.write_text(
                json.dumps(
                    {"stories": [story_for_issue(116, status="pushed", prNumber=349)]}
                )
            )
            return closed_issue()

        monkeypatch.setattr(sync_closed_issues, "fetch_issue", claim_it_meanwhile)
        monkeypatch.setattr(
            sys,
            "argv",
            ["sync-closed-issues-to-prd.py", "--prd", str(prd_path)],
        )
        assert sync_closed_issues.main() == 0
        stories = json.loads(prd_path.read_text())["stories"]
        assert stories[0]["status"] == "pushed"

    def test_a_missing_prd_is_an_error(self, sync_closed_issues, monkeypatch, tmp_path):
        monkeypatch.setattr(
            sys,
            "argv",
            ["sync-closed-issues-to-prd.py", "--prd", str(tmp_path / "nope.json")],
        )
        assert sync_closed_issues.main() == 2


# ═══════════════════════════════════════════════════════════════════════════
# add-backlog-to-prd.py
# ═══════════════════════════════════════════════════════════════════════════

ADD_BACKLOG_SCRIPT = os.path.join(SCRIPT_DIR, "add-backlog-to-prd.py")


def make_issue(number=52439, title="Something is broken", labels=()):
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/brave/brave-browser/issues/{number}",
        "labels": [{"name": name} for name in labels],
    }


def run_add_backlog(prd_path, issues, *args, archived_path=None):
    """Run add-backlog-to-prd.py against a fixed issue list (no gh calls)."""
    issues_path = prd_path + ".issues.json"
    with open(issues_path, "w") as f:
        json.dump(issues, f)
    cmd = [
        sys.executable,
        ADD_BACKLOG_SCRIPT,
        "--prd",
        prd_path,
        "--archived-prd",
        archived_path or (prd_path + ".missing.json"),
        "--issues-file",
        issues_path,
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


class TestAddBacklogClassification:
    def test_test_failure_title_is_a_test_issue(self, add_backlog):
        issue = make_issue(title="Test failure: FooTest.Bar")
        assert add_backlog.is_test_issue(issue) is True
        assert add_backlog.is_disabled_test_issue(issue) is False

    def test_test_label_is_a_test_issue(self, add_backlog):
        issue = make_issue(title="Flaky somewhere", labels=("bot/type/test",))
        assert add_backlog.is_test_issue(issue) is True

    def test_disabled_test_title_is_a_disabled_issue(self, add_backlog):
        issue = make_issue(title="Disabled test: FooTest.Bar")
        assert add_backlog.is_disabled_test_issue(issue) is True

    def test_disabled_test_label_is_a_disabled_issue(self, add_backlog, monkeypatch):
        """The label is project-specific, so the profile decides what it is."""
        monkeypatch.setattr(
            add_backlog,
            "_profile",
            {"labels": {"disabledTest": "disabled-brave-test"}},
        )
        issue = make_issue(
            title="Re-enable FooTest.Bar", labels=("disabled-brave-test",)
        )
        assert add_backlog.is_disabled_test_issue(issue) is True

    def test_disabled_test_label_ignored_when_profile_defines_none(
        self, add_backlog, monkeypatch
    ):
        """A project with no such label must not inherit Brave's."""
        monkeypatch.setattr(add_backlog, "_profile", {"labels": {"disabledTest": ""}})
        issue = make_issue(
            title="Re-enable FooTest.Bar", labels=("disabled-brave-test",)
        )
        assert add_backlog.is_disabled_test_issue(issue) is False

    def test_disabled_test_label_falls_back_to_config(self, add_backlog, monkeypatch):
        """Deployments that set labels.disabledTestLabel by hand keep working."""
        monkeypatch.setattr(add_backlog, "_profile", {})
        monkeypatch.setattr(
            add_backlog, "_config", {"labels": {"disabledTestLabel": "legacy-label"}}
        )
        issue = make_issue(title="Re-enable FooTest.Bar", labels=("legacy-label",))
        assert add_backlog.is_disabled_test_issue(issue) is True

    def test_plain_issue_is_neither(self, add_backlog):
        issue = make_issue(title="Add a settings toggle")
        assert add_backlog.is_test_issue(issue) is False
        assert add_backlog.is_disabled_test_issue(issue) is False


class TestAddBacklogTestNameExtraction:
    def test_disabled_test_prefix(self, add_backlog):
        assert (
            add_backlog.extract_disabled_test_name("Disabled test: FooTest.Bar")
            == "FooTest.Bar"
        )

    def test_backtick_quoted_name(self, add_backlog):
        assert (
            add_backlog.extract_disabled_test_name(
                "Flaky `FooTest/FooTest.Bar/1` again"
            )
            == "FooTest/FooTest.Bar/1"
        )

    def test_re_enable_phrasing(self, add_backlog):
        assert add_backlog.extract_disabled_test_name("Re-enable FooTest.Bar test") == (
            "FooTest.Bar"
        )

    def test_search_term_strips_param_suffix_and_class(self, add_backlog):
        assert (
            add_backlog.extract_disabled_search_term("FooTest/FooTest.Bar/1") == "Bar"
        )

    def test_search_term_of_bare_name(self, add_backlog):
        assert add_backlog.extract_disabled_search_term("Bar") == "Bar"


class TestAddBacklogTracking:
    def test_description_issue_reference_is_tracked(self, add_backlog):
        story = make_story(description="Resolve issue #52439: x")
        assert add_backlog.stories_by_issue({"stories": [story]}) == {52439: [story]}

    def test_archived_prd_counts_as_tracked(self, add_backlog):
        archived = {"stories": [make_story(description="Resolve issue #111: x")]}
        assert 111 in add_backlog.stories_by_issue({"stories": []}, archived)

    def test_missing_archived_prd_is_tolerated(self, add_backlog):
        assert add_backlog.stories_by_issue({"stories": []}, None) == {}

    def test_unrelated_hash_is_not_tracked(self, add_backlog):
        prd = {"stories": [make_story(description="Land PR #52439 in repo")]}
        assert add_backlog.stories_by_issue(prd) == {}


class TestAddBacklogOnlyPartlyLanded:
    """An open issue whose every story merged was left open by a `Part of` pull
    request, and the rest of it is work nobody is holding."""

    def test_every_story_merged(self, add_backlog):
        assert add_backlog.only_partly_landed([make_story(status="merged")]) is True

    def test_no_story_at_all(self, add_backlog):
        assert add_backlog.only_partly_landed([]) is False

    @pytest.mark.parametrize("status", ["pending", "committed", "pushed"])
    def test_work_in_flight(self, add_backlog, status):
        stories = [make_story(status="merged"), make_story(status=status)]
        assert add_backlog.only_partly_landed(stories) is False

    @pytest.mark.parametrize("status", ["skipped", "invalid"])
    def test_a_judgment_not_to_work_it(self, add_backlog, status):
        assert add_backlog.only_partly_landed([make_story(status=status)]) is False

    def test_absent_status_reads_as_pending(self, add_backlog):
        story = make_story()
        del story["status"]
        assert add_backlog.only_partly_landed([story]) is False


class TestAddBacklogEndToEnd:
    def test_appends_missing_issue(self, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": []})
        result = run_add_backlog(prd_path, [make_issue(number=900, title="Fix thing")])
        assert result.returncode == 0
        stories = read_json(prd_path)["stories"]
        assert len(stories) == 1
        assert stories[0]["id"] == "US-001"
        assert stories[0]["status"] == "pending"
        assert "issue #900" in stories[0]["description"]
        assert json.loads(result.stdout)["added"][0]["issueNumber"] == 900

    def test_ids_and_priorities_continue_from_existing(self, write_json, read_json):
        prd_path = write_json(
            "prd.json", {"stories": [make_story(id="US-007", priority=7)]}
        )
        run_add_backlog(prd_path, [make_issue(number=901, title="Fix thing")])
        stories = read_json(prd_path)["stories"]
        assert stories[1]["id"] == "US-008"
        assert stories[1]["priority"] == 8

    def test_already_tracked_issue_is_skipped(self, write_json, read_json):
        prd_path = write_json(
            "prd.json",
            {"stories": [make_story(description="Resolve issue #902: Fix thing")]},
        )
        result = run_add_backlog(prd_path, [make_issue(number=902, title="Fix thing")])
        assert read_json(prd_path)["stories"] == [
            make_story(description="Resolve issue #902: Fix thing")
        ]
        assert json.loads(result.stdout)["added"] == []

    def test_archived_issue_is_not_re_added(self, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": []})
        archived_path = write_json(
            "prd.archived.json",
            {
                "stories": [
                    make_story(status="skipped", description="Resolve issue #903: x")
                ]
            },
        )
        run_add_backlog(
            prd_path,
            [make_issue(number=903, title="x")],
            archived_path=archived_path,
        )
        assert read_json(prd_path)["stories"] == []

    def test_dry_run_writes_nothing(self, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": []})
        result = run_add_backlog(
            prd_path, [make_issue(number=904, title="Fix thing")], "--dry-run"
        )
        assert read_json(prd_path)["stories"] == []
        summary = json.loads(result.stdout)
        assert summary["dryRun"] is True
        assert len(summary["added"]) == 1

    def test_missing_prd_is_created(self, tmp_dir, read_json):
        prd_path = os.path.join(tmp_dir, "new-prd.json")
        result = run_add_backlog(prd_path, [make_issue(number=905, title="Fix thing")])
        assert result.returncode == 0
        prd = read_json(prd_path)
        assert prd["projectName"].endswith("Backlog")
        assert len(prd["stories"]) == 1

    def test_a_first_pass_is_not_described_as_finishing_one(
        self, write_json, read_json
    ):
        prd_path = write_json("prd.json", {"stories": []})
        result = run_add_backlog(prd_path, [make_issue(number=906, title="Fix thing")])
        story = read_json(prd_path)["stories"][0]
        assert story["description"] == "Resolve issue #906: Fix thing"
        assert json.loads(result.stdout)["finishing"] == {}


class TestAddBacklogFinishesPartialWork:
    """A `Part of` pull request merges and leaves its issue open. The issue is
    still assigned and still has work in it, so it comes back — carrying what
    landed, which its own body does not mention."""

    def merged(self, number=902, id="US-007", pr=307):
        return make_story(
            id=id,
            status="merged",
            prNumber=pr,
            description=f"Resolve issue #{number}: Fix thing",
        )

    def test_issue_left_open_by_a_merge_comes_back(self, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": [self.merged()]})
        result = run_add_backlog(prd_path, [make_issue(number=902, title="Fix thing")])
        assert result.returncode == 0
        stories = read_json(prd_path)["stories"]
        assert len(stories) == 2
        assert stories[1]["status"] == "pending"
        assert stories[1]["description"] == (
            "Finish issue #902: Fix thing. "
            "PR #307 (US-007) merged against it already and left it open."
        )
        assert json.loads(result.stdout)["finishing"] == {"902": "PR #307 (US-007)"}

    def test_it_says_what_not_to_write_twice(self, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": [self.merged()]})
        run_add_backlog(prd_path, [make_issue(number=902, title="Fix thing")])
        criteria = read_json(prd_path)["stories"][1]["acceptanceCriteria"]
        assert any(
            "PR #307 (US-007)" in c and "must not be written a second time" in c
            for c in criteria
        )
        assert any("mark this story invalid" in c for c in criteria)

    def test_every_merged_story_is_named(self, write_json, read_json):
        prd_path = write_json(
            "prd.json",
            {
                "stories": [
                    self.merged(id="US-007", pr=307),
                    self.merged(id="US-008", pr=311),
                ]
            },
        )
        run_add_backlog(prd_path, [make_issue(number=902, title="Fix thing")])
        story = read_json(prd_path)["stories"][2]
        assert "PR #307 (US-007), PR #311 (US-008)" in story["description"]

    def test_a_merged_story_without_a_pr_is_named_by_its_id(
        self, write_json, read_json
    ):
        merged = self.merged()
        merged["prNumber"] = None
        prd_path = write_json("prd.json", {"stories": [merged]})
        run_add_backlog(prd_path, [make_issue(number=902, title="Fix thing")])
        story = read_json(prd_path)["stories"][1]
        assert "US-007 merged against it already" in story["description"]

    def test_an_archived_merged_story_still_brings_it_back(self, write_json, read_json):
        prd_path = write_json("prd.json", {"stories": []})
        archived_path = write_json("prd.archived.json", {"stories": [self.merged()]})
        run_add_backlog(
            prd_path,
            [make_issue(number=902, title="Fix thing")],
            archived_path=archived_path,
        )
        stories = read_json(prd_path)["stories"]
        assert len(stories) == 1
        assert stories[0]["description"].startswith("Finish issue #902")

    @pytest.mark.parametrize("status", ["pending", "committed", "pushed"])
    def test_work_in_flight_is_left_alone(self, write_json, read_json, status):
        story = self.merged()
        story["status"] = status
        prd_path = write_json("prd.json", {"stories": [story]})
        result = run_add_backlog(prd_path, [make_issue(number=902, title="Fix thing")])
        assert read_json(prd_path)["stories"] == [story]
        assert json.loads(result.stdout)["alreadyTracked"] == 1

    @pytest.mark.parametrize("status", ["skipped", "invalid"])
    def test_a_decision_not_to_work_it_is_not_overturned(
        self, write_json, read_json, status
    ):
        story = self.merged()
        story["status"] = status
        prd_path = write_json("prd.json", {"stories": [story]})
        run_add_backlog(prd_path, [make_issue(number=902, title="Fix thing")])
        assert read_json(prd_path)["stories"] == [story]

    def test_one_merged_story_does_not_carry_a_skipped_one(self, write_json, read_json):
        skipped = self.merged(id="US-008", pr=311)
        skipped["status"] = "skipped"
        prd_path = write_json("prd.json", {"stories": [self.merged(), skipped]})
        run_add_backlog(prd_path, [make_issue(number=902, title="Fix thing")])
        assert len(read_json(prd_path)["stories"]) == 2


class TestAddBacklogTriage:
    """Stories carry the axes their issue was labelled with, so select-task.py
    can order the backlog by them instead of by the order issues arrived in."""

    AXES = {
        "importance": "importance/p",
        "urgency": "urgency/p",
        "size": "size/",
    }

    @pytest.fixture
    def labelled_profile(self, add_backlog, monkeypatch):
        """A profile that spells the axes. The module binds its profile at
        import from the live config.json, so pin one or this suite means
        something different on every deployment."""
        monkeypatch.setattr(add_backlog, "_profile", {"labels": {"axes": self.AXES}})
        return add_backlog

    def test_story_carries_the_issues_axes(self, labelled_profile):
        issue = make_issue(
            number=910, title="Do a thing", labels=("importance/p2", "urgency/p3")
        )
        story = labelled_profile.build_story(1, 1, issue)
        assert story["triage"] == {"importance": 2, "urgency": 3}

    def test_test_stories_carry_them_too(self, labelled_profile, monkeypatch):
        """Every story kind goes through build_story, so none of the three can
        quietly lose the axes."""
        monkeypatch.setattr(labelled_profile, "find_test_location", lambda _: "brave")
        for title in ("Test failure: Foo.Bar", "Disabled test: Foo.Bar", "Do a thing"):
            story = labelled_profile.build_story(
                1, 1, make_issue(number=911, title=title, labels=("size/4",))
            )
            assert story["triage"] == {"size": 4}, title

    def test_unlabelled_issue_gets_no_triage_block(self, labelled_profile):
        """An empty block would claim the issue was judged and found middling."""
        story = labelled_profile.build_story(1, 1, make_issue(number=912))
        assert "triage" not in story

    def test_project_without_axes_reads_none(self, add_backlog, monkeypatch):
        """brave-core does not label its issues this way, and must not inherit
        bravebot's labels by accident."""
        monkeypatch.setattr(add_backlog, "_profile", {"labels": {}})
        story = add_backlog.build_story(
            1, 1, make_issue(number=913, labels=("importance/p1",))
        )
        assert "triage" not in story


class TestAddBacklogRetriage:
    """The axes move after intake -- somebody raises an urgency, or sizes an
    issue that arrived unjudged. This script is the only thing that reads them,
    so a story left with the labels of the day it was created is ordered by
    nothing anybody can still see."""

    @pytest.fixture
    def labelled_profile(self, add_backlog, monkeypatch):
        monkeypatch.setattr(
            add_backlog,
            "_profile",
            {"labels": {"axes": TestAddBacklogTriage.AXES}},
        )
        return add_backlog

    def test_pending_story_follows_its_issue(self, labelled_profile):
        story = make_story(
            "pending",
            description="Resolve issue #920: x",
            triage={"urgency": 4},
        )
        moved = labelled_profile.refresh_triage(
            [story], [make_issue(number=920, labels=("urgency/p2",))]
        )
        assert story["triage"] == {"urgency": 2}
        assert moved == [
            {
                "id": "US-001",
                "issueNumber": 920,
                "from": {"urgency": 4},
                "to": {"urgency": 2},
            }
        ]

    def test_unchanged_triple_is_not_reported(self, labelled_profile):
        story = make_story(
            "pending", description="Resolve issue #921: x", triage={"urgency": 2}
        )
        moved = labelled_profile.refresh_triage(
            [story], [make_issue(number=921, labels=("urgency/p2",))]
        )
        assert moved == []

    def test_labels_removed_from_the_issue_are_removed_here(self, labelled_profile):
        story = make_story(
            "pending", description="Resolve issue #922: x", triage={"urgency": 2}
        )
        labelled_profile.refresh_triage([story], [make_issue(number=922)])
        assert "triage" not in story

    def test_work_that_has_a_pr_is_left_alone(self, labelled_profile):
        """A pushed story's place in the queue is decided by its PR, not by the
        axes, and rewriting a story mid-review is not this script's business."""
        story = make_story(
            "pushed", description="Resolve issue #923: x", triage={"urgency": 4}
        )
        moved = labelled_profile.refresh_triage(
            [story], [make_issue(number=923, labels=("urgency/p1",))]
        )
        assert story["triage"] == {"urgency": 4}
        assert moved == []

    def test_story_whose_issue_is_gone_is_left_alone(self, labelled_profile):
        """The fetch is open-and-assigned only, so a closed or reassigned issue
        is simply absent -- which is not the same as having lost its labels."""
        story = make_story(
            "pending", description="Resolve issue #924: x", triage={"urgency": 2}
        )
        labelled_profile.refresh_triage([story], [])
        assert story["triage"] == {"urgency": 2}

    def test_project_without_axes_rewrites_nothing(self, add_backlog, monkeypatch):
        monkeypatch.setattr(add_backlog, "_profile", {"labels": {}})
        story = make_story(
            "pending", description="Resolve issue #925: x", triage={"urgency": 2}
        )
        assert add_backlog.refresh_triage([story], [make_issue(number=925)]) == []
        assert story["triage"] == {"urgency": 2}

    def test_safety_check_still_catches_every_other_field(self, add_backlog):
        """The axes are the one field this script may rewrite on a story it did
        not create. The check that guards the rest has to still fire."""
        story = make_story("pending", triage={"urgency": 2})
        assert add_backlog.without_triage(story) == make_story("pending")
        edited = make_story("skipped", triage={"urgency": 2})
        assert add_backlog.without_triage(edited) != add_backlog.without_triage(story)

    def test_end_to_end_rewrites_the_block_and_nothing_else(
        self, write_json, read_json
    ):
        """Through the script as run.sh runs it, against whatever profile this
        deployment has: the story is either re-triaged or untouched, and either
        way every other field survives."""
        story = make_story("pending", description="Resolve issue #926: x")
        prd_path = write_json("prd.json", {"stories": [story]})
        result = run_add_backlog(
            prd_path,
            [make_issue(number=926, labels=("importance/p2", "urgency/p3", "size/2"))],
        )
        assert result.returncode == 0
        written = read_json(prd_path)["stories"][0]
        assert json.loads(result.stdout)["added"] == []
        assert {k: v for k, v in written.items() if k != "triage"} == story


# ── scripts/lib/triage.py ────────────────────────────────────────────────────


class TestTriageLib:
    """The axis vocabulary both scripts read. What each value *means* is the
    project's to define; what is fixed here is the range, the reading order,
    and what an unjudged axis does."""

    @staticmethod
    def _lib():
        sys.path.insert(0, SCRIPT_DIR)
        from lib import triage

        return triage

    BRAVEBOT_PREFIXES = {
        "importance": "importance/p",
        "urgency": "urgency/p",
        "size": "size/",
    }

    def test_reads_a_full_triple(self):
        got = self._lib().read_labels(
            ["bug", "importance/p2", "urgency/p3", "size/2"], self.BRAVEBOT_PREFIXES
        )
        assert got == {"importance": 2, "urgency": 3, "size": 2}

    def test_unlabelled_axis_is_absent_not_defaulted(self):
        """Absent has to stay distinguishable from judged-middling: only the
        ordering gets to decide what nobody judging it means."""
        got = self._lib().read_labels(["importance/p1"], self.BRAVEBOT_PREFIXES)
        assert got == {"importance": 1}

    def test_value_outside_the_range_is_ignored(self):
        """A sixth level is somebody's typo, not a value."""
        got = self._lib().read_labels(
            ["importance/p0", "size/10", "urgency/p6"], self.BRAVEBOT_PREFIXES
        )
        assert got == {}

    def test_non_numeric_suffix_is_ignored(self):
        got = self._lib().read_labels(["size/large"], self.BRAVEBOT_PREFIXES)
        assert got == {}

    def test_case_is_not_load_bearing(self):
        """Some repos spell the value P2 and some p2. Either is the same axis."""
        got = self._lib().read_labels(["Importance/P4"], self.BRAVEBOT_PREFIXES)
        assert got == {"importance": 4}

    def test_duplicate_labels_take_the_most_severe(self):
        """The axes are ordinary labels, so both values can survive a botched
        edit. Which one wins must not depend on the order the API listed them."""
        lib = self._lib()
        assert lib.read_labels(
            ["urgency/p4", "urgency/p2"], self.BRAVEBOT_PREFIXES
        ) == {"urgency": 2}
        assert lib.read_labels(
            ["urgency/p2", "urgency/p4"], self.BRAVEBOT_PREFIXES
        ) == {"urgency": 2}

    def test_prefixes_come_from_the_profile(self):
        lib = self._lib()
        profile = {"labels": {"axes": self.BRAVEBOT_PREFIXES}}
        assert lib.axis_prefixes(profile) == self.BRAVEBOT_PREFIXES

    def test_project_without_axes_has_no_prefixes(self):
        lib = self._lib()
        assert lib.axis_prefixes({}) == {}
        assert lib.axis_prefixes({"labels": {"pr": []}}) == {}

    def test_blank_prefix_is_dropped(self):
        """A half-filled mapping labels what it names and ignores the rest --
        an empty prefix would otherwise match every label there is."""
        lib = self._lib()
        got = lib.axis_prefixes({"labels": {"axes": {"size": "", "urgency": "u/"}}})
        assert got == {"urgency": "u/"}

    def test_rank_is_urgency_then_importance(self):
        assert self._lib().rank({"importance": 1, "urgency": 2}) == (2.0, 1.0)

    def test_rank_of_an_unjudged_story_is_neutral(self):
        lib = self._lib()
        assert lib.rank({}) == (lib.NEUTRAL, lib.NEUTRAL)
        assert lib.rank(None) == (lib.NEUTRAL, lib.NEUTRAL)

    def test_rank_ignores_size(self):
        lib = self._lib()
        assert lib.rank({"size": 5}) == (lib.NEUTRAL, lib.NEUTRAL)

    def test_unreadable_value_ranks_as_unjudged(self):
        lib = self._lib()
        assert lib.value("nonsense") == lib.NEUTRAL
        assert lib.value(None) == lib.NEUTRAL
        assert lib.value(9) == lib.NEUTRAL

    def test_format_uses_reading_order_not_key_order(self):
        got = self._lib().format_triage({"size": 2, "urgency": 3, "importance": 2})
        assert got == "importance 2, urgency 3, size 2"

    def test_format_of_nothing_is_empty(self):
        lib = self._lib()
        assert lib.format_triage({}) == ""
        assert lib.format_triage(None) == ""


# ── resolve_target_repo ──────────────────────────────────────────────────────


class TestResolveTargetRepo:
    """project.targetRepoPath is stored against two different bases in the
    wild: config.brave-core.json uses "src/brave" (relative to the bot dir's
    parent) while the setup wizard documents a bot-dir-relative path. Both
    must keep resolving, and the shell and Python resolvers must agree."""

    @staticmethod
    def _resolver():
        sys.path.insert(0, SCRIPT_DIR)
        from lib.load_config import resolve_target_repo

        return resolve_target_repo

    @staticmethod
    def _layout(root, target_rel):
        """Create <root>/brave-dev-loop and a git repo at <root>/<target_rel>."""
        bot = os.path.join(root, "brave-dev-loop")
        target = os.path.join(root, target_rel)
        os.makedirs(os.path.join(target, ".git"))
        os.makedirs(bot, exist_ok=True)
        return bot, target

    def _sh_resolve(self, bot_dir, raw_path):
        """Run the bash resolver so both implementations stay in sync."""
        os.makedirs(os.path.join(bot_dir, "scripts", "lib"), exist_ok=True)
        src = os.path.join(SCRIPT_DIR, "lib", "load-config.sh")
        dst = os.path.join(bot_dir, "scripts", "lib", "load-config.sh")
        with open(src) as f:
            contents = f.read()
        with open(dst, "w") as f:
            f.write(contents)
        with open(os.path.join(bot_dir, "config.json"), "w") as f:
            json.dump(
                {
                    "project": {
                        "name": "p",
                        "org": "o",
                        "prRepository": "o/p",
                        "issueRepository": "o/p",
                        "targetRepoPath": raw_path,
                    },
                    "bot": {"username": "b"},
                },
                f,
            )
        probe = os.path.join(bot_dir, "probe.sh")
        with open(probe, "w") as f:
            f.write(
                '#!/bin/bash\nsource "$(dirname "$0")/scripts/lib/load-config.sh"\n'
                'printf "%s" "$BOT_TARGET_REPO_DIR"\n'
            )
        os.chmod(probe, 0o755)
        return subprocess.run(
            [probe], capture_output=True, text=True, check=True
        ).stdout

    def test_parent_relative_brave_core_layout(self, tmp_dir):
        """ "src/brave" next to the bot dir — the shipped brave-core spelling."""
        bot, target = self._layout(tmp_dir, os.path.join("src", "brave"))
        cfg = {"project": {"targetRepoPath": "src/brave"}}
        assert self._resolver()(cfg, bot) == target

    def test_bot_relative_layout(self, tmp_dir):
        """ "../sibling" relative to the bot dir — what the wizard documents."""
        bot, target = self._layout(tmp_dir, "sibling")
        cfg = {"project": {"targetRepoPath": "../sibling"}}
        assert self._resolver()(cfg, bot) == target

    def test_absolute_path_passthrough(self):
        cfg = {"project": {"targetRepoPath": "/abs/target"}}
        assert self._resolver()(cfg, "/bot") == "/abs/target"

    def test_unset_returns_none(self):
        assert self._resolver()({}, "/bot") is None

    def test_missing_repo_falls_back_to_documented_base(self, tmp_dir):
        """Neither base exists — return the bot-dir base so errors read sanely."""
        bot = os.path.join(tmp_dir, "brave-dev-loop")
        os.makedirs(bot)
        cfg = {"project": {"targetRepoPath": "nope"}}
        assert self._resolver()(cfg, bot) == os.path.join(bot, "nope")

    def test_shell_and_python_agree_parent_base(self, tmp_dir):
        bot, target = self._layout(tmp_dir, os.path.join("src", "brave"))
        cfg = {"project": {"targetRepoPath": "src/brave"}}
        assert self._sh_resolve(bot, "src/brave") == target
        assert self._resolver()(cfg, bot) == target

    def test_shell_and_python_agree_bot_base(self, tmp_dir):
        bot, target = self._layout(tmp_dir, "sibling")
        cfg = {"project": {"targetRepoPath": "../sibling"}}
        assert self._sh_resolve(bot, "../sibling") == target
        assert self._resolver()(cfg, bot) == target


# ── repair-config-paths.py ───────────────────────────────────────────────────


class TestRepairConfigPaths:
    """`make setup` repairs a docsDir left pointing nowhere by the historical
    targetRepoPath base ambiguity. Must be a no-op on healthy configs."""

    @staticmethod
    def _layout(root, target_rel, docs=True):
        bot = os.path.join(root, "brave-dev-loop")
        target = os.path.join(root, target_rel)
        os.makedirs(os.path.join(target, ".git"))
        if docs:
            os.makedirs(os.path.join(target, "docs"))
        os.makedirs(bot, exist_ok=True)
        return bot, target

    def test_repairs_broken_docs_dir(self, repair_config_paths, tmp_dir):
        bot, _ = self._layout(tmp_dir, os.path.join("src", "brave"))
        cfg = {
            "project": {"targetRepoPath": "src/brave"},
            "bestPractices": {"docsDir": "../../src/brave/docs"},
        }
        fixed, old = repair_config_paths.repair(cfg, bot)
        assert fixed == os.path.join("..", "src", "brave", "docs")
        assert old == "../../src/brave/docs"
        assert cfg["bestPractices"]["docsDir"] == fixed

    def test_noop_when_docs_dir_already_resolves(self, repair_config_paths, tmp_dir):
        bot, _ = self._layout(tmp_dir, os.path.join("src", "brave"))
        cfg = {
            "project": {"targetRepoPath": "src/brave"},
            "bestPractices": {"docsDir": "../src/brave/docs"},
        }
        fixed, reason = repair_config_paths.repair(cfg, bot)
        assert fixed is None
        assert reason == "already resolves"
        assert cfg["bestPractices"]["docsDir"] == "../src/brave/docs"

    def test_noop_when_target_repo_missing(self, repair_config_paths, tmp_dir):
        bot = os.path.join(tmp_dir, "brave-dev-loop")
        os.makedirs(bot)
        cfg = {
            "project": {"targetRepoPath": "nowhere"},
            "bestPractices": {"docsDir": "../../nope/docs"},
        }
        fixed, reason = repair_config_paths.repair(cfg, bot)
        assert fixed is None
        assert reason == "target repo not found"
        assert cfg["bestPractices"]["docsDir"] == "../../nope/docs"

    def test_repairs_bot_relative_layout(self, repair_config_paths, tmp_dir):
        bot, _ = self._layout(tmp_dir, "sibling")
        cfg = {
            "project": {"targetRepoPath": "../sibling"},
            "bestPractices": {"docsDir": "../../sibling/docs"},
        }
        fixed, _ = repair_config_paths.repair(cfg, bot)
        assert fixed == os.path.join("..", "sibling", "docs")

    def test_idempotent_across_two_runs(self, repair_config_paths, tmp_dir):
        bot, _ = self._layout(tmp_dir, os.path.join("src", "brave"))
        cfg = {
            "project": {"targetRepoPath": "src/brave"},
            "bestPractices": {"docsDir": "../../src/brave/docs"},
        }
        first, _ = repair_config_paths.repair(cfg, bot)
        second, reason = repair_config_paths.repair(cfg, bot)
        assert second is None and reason == "already resolves"
        assert cfg["bestPractices"]["docsDir"] == first

    def test_writes_valid_json_end_to_end(self, repair_config_paths, tmp_dir):
        """Guards the inline-python-in-bash escaping bug this was extracted from."""
        bot, _ = self._layout(tmp_dir, os.path.join("src", "brave"))
        config_path = os.path.join(bot, "config.json")
        with open(config_path, "w") as f:
            json.dump(
                {
                    "project": {"targetRepoPath": "src/brave"},
                    "bestPractices": {"docsDir": "../../src/brave/docs"},
                },
                f,
            )
        script = os.path.join(SCRIPT_DIR, "repair-config-paths.py")
        for _ in range(2):
            res = subprocess.run(
                [sys.executable, script, "--config", config_path, "--bot-root", bot],
                capture_output=True,
                text=True,
            )
            assert res.returncode == 0, res.stderr
            with open(config_path) as f:
                reloaded = json.load(f)
        assert reloaded["bestPractices"]["docsDir"] == os.path.join(
            "..", "src", "brave", "docs"
        )


class TestShippedConfigs:
    """The reference configs pin behaviour for deployments seeded from them."""

    @staticmethod
    def _load(name):
        with open(os.path.join(os.path.dirname(__file__), os.pardir, name)) as f:
            return json.load(f)

    def test_brave_core_keeps_the_fork_layout(self):
        """brave-core pushes from a fork; flipping this silently rewires its remotes."""
        assert self._load("config.brave-core.json")["project"]["useFork"] is True

    def test_example_defaults_to_fork_layout(self):
        assert self._load("config.example.json")["project"]["useFork"] is True

    def test_brave_dev_loop_targets_the_bot_directory(self):
        """The self-hosted layout: the loop develops the checkout it runs from.

        A targetRepoPath pointing elsewhere, or a fork, would send the stories to
        a second copy of this repository -- and the project's docs, which put
        every story in a worktree of the bot directory, describe this one."""
        project = self._load("config.brave-dev-loop.json")["project"]
        assert project["targetRepoPath"] == "."
        assert project["useFork"] is False

    def test_docs_dir_matches_target_repo_in_reference_configs(self):
        """docsDir is bot-dir-relative; targetRepoPath may be either base."""
        for name in _reference_configs():
            cfg = self._load(name)
            target = cfg["project"]["targetRepoPath"].lstrip("./")
            docs = cfg["bestPractices"]["docsDir"]
            if not target:
                # targetRepoPath "." — the bot dir is the target repo, so the
                # bot-dir-relative docsDir has no prefix in front of it.
                assert docs == "docs", (name, docs)
                continue
            assert docs.endswith("/docs"), (name, docs)
            assert target.split("/")[-1] in docs, (name, target, docs)


# ── Project profiles ─────────────────────────────────────────────────────────

# The exact acceptance-criteria tail add-backlog-to-prd.py emitted before the
# validations moved into projects/brave-core/profile.json. brave-core bots must
# keep getting these byte-for-byte.
_BC_REVIEW = (
    "Commit changes, then run the /review skill from the target repo in a fresh "
    "subagent (read .claude/skills/review/SKILL.md and follow Local Mode steps); "
    "report all findings back to the main context; fix any violations and commit "
    "the fixes (must pass)"
)
_BC_FORMAT = (
    "Run pnpm run format one final time; if it makes any changes, amend the last "
    "commit with the formatting fixes"
)


def _bc_tail(test_step):
    return [
        "Build the project (must pass)",
        "Format the code (must pass)",
        _BC_REVIEW,
        test_step,
        "Run presubmit checks (must pass)",
        _BC_FORMAT,
    ]


class TestBraveCoreProfileIsUnchanged:
    """Guards the promise that moving validations into a profile changed
    nothing for brave-core. Compares against strings captured pre-refactor."""

    @pytest.fixture(autouse=True)
    def _pin_brave_core(self, add_backlog, monkeypatch):
        """The module binds its profile at import from the live config.json.
        Pin brave-core so this suite means the same on every deployment."""
        sys.path.insert(0, SCRIPT_DIR)
        from lib.load_config import load_profile

        monkeypatch.setattr(
            add_backlog,
            "_profile",
            load_profile({"project": {"profile": "brave-core"}}),
        )

    def test_test_failure_story_tail(self, add_backlog, monkeypatch):
        monkeypatch.setattr(add_backlog, "find_test_location", lambda _: "brave")
        story = add_backlog.build_test_story(
            1, 2, {"number": 42, "title": "Test failure: Foo.Bar"}
        )
        assert story["acceptanceCriteria"][-6:] == _bc_tail(
            "Run the test: brave_browser_tests --gtest_filter=Foo.Bar (must pass - "
            "run 5 times to verify consistency, unless this is a filter file change only)"
        )

    def test_disabled_test_story_tail(self, add_backlog, monkeypatch):
        monkeypatch.setattr(add_backlog, "find_test_location", lambda _: "brave")
        story = add_backlog.build_disabled_test_story(
            1, 2, {"number": 42, "title": "Disabled test: Foo.Bar"}
        )
        assert story["acceptanceCriteria"][-6:] == _bc_tail(
            "Run the test: brave_browser_tests --gtest_filter=Foo.Bar "
            "(must pass - run 5 times to verify consistency)"
        )

    def test_generic_story_tail(self, add_backlog):
        story = add_backlog.build_generic_story(
            1, 2, {"number": 42, "title": "Do a thing"}
        )
        assert story["acceptanceCriteria"][-6:] == _bc_tail(
            "Find and run relevant tests to verify the change (must pass)"
        )

    def test_upstream_test_uses_unprefixed_binary(self, add_backlog, monkeypatch):
        monkeypatch.setattr(add_backlog, "find_test_location", lambda _: "chromium")
        story = add_backlog.build_test_story(
            1, 2, {"number": 42, "title": "Test failure: Foo.Bar"}
        )
        assert "browser_tests --gtest_filter=Foo.Bar" in story["acceptanceCriteria"][-3]
        assert "brave_browser_tests" not in story["acceptanceCriteria"][-3]

    def test_unit_suite_selection(self, add_backlog, monkeypatch):
        monkeypatch.setattr(add_backlog, "find_test_location", lambda _: "brave")
        story = add_backlog.build_test_story(
            1, 2, {"number": 42, "title": "Test failure: Foo.PartitionAlloc"}
        )
        assert story["testType"] == "unit_test"
        assert "brave_unit_tests" in story["acceptanceCriteria"][-3]


class TestProfileLoading:
    @staticmethod
    def _lib():
        sys.path.insert(0, SCRIPT_DIR)
        import lib.load_config as m

        return m

    def test_absent_profile_defaults_to_brave_core(self):
        """Deployments predating profiles must not change behaviour."""
        m = self._lib()
        assert m.profile_dir({}).endswith(os.path.join("projects", "brave-core"))

    def test_named_profile_is_used(self):
        m = self._lib()
        got = m.profile_dir({"project": {"profile": "default"}})
        assert got.endswith(os.path.join("projects", "default"))

    def test_unknown_profile_falls_back_to_default_profile_json(self):
        """A half-created profile directory must not strand a bot."""
        m = self._lib()
        profile = m.load_profile({"project": {"profile": "does-not-exist"}})
        assert profile.get("validations")

    def test_test_step_placeholder_dropped_when_absent(self):
        m = self._lib()
        profile = {"validations": ["a", "{testStep}", "b"]}
        assert m.build_validations(profile, None) == ["a", "b"]
        assert m.build_validations(profile, "run it") == ["a", "run it", "b"]

    def test_default_profile_has_no_chromium_assumptions(self):
        m = self._lib()
        profile = m.load_profile({"project": {"profile": "default"}})
        blob = json.dumps(profile).lower()
        for term in ("pnpm", "gtest", "chromium", "brave", "presubmit"):
            assert term not in blob, f"default profile leaks {term!r}"


class TestProfileResearch:
    """The "read this first" step is profile-owned. It has to be: pointing a
    story at a best_practices.md that only brave-core has was the bug that
    prompted the key."""

    @staticmethod
    def _lib():
        sys.path.insert(0, SCRIPT_DIR)
        import lib.load_config as m

        return m

    def test_best_practices_placeholder_becomes_absolute_path(self, tmp_path):
        m = self._lib()
        docs = tmp_path / "repo" / "docs"
        docs.mkdir(parents=True)
        config = {
            "bestPractices": {"docsDir": "repo/docs", "indexFile": "bp.md"},
        }
        got = m.build_research(
            {"research": ["Read {bestPractices} first"]}, config, str(tmp_path)
        )
        assert got == [f"Read {docs / 'bp.md'} first"]

    def test_target_repo_placeholder_becomes_absolute_path(self, tmp_path):
        m = self._lib()
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        config = {"project": {"targetRepoPath": "repo"}}
        got = m.build_research(
            {"research": ["Read {targetRepo}/AGENTS.md"]}, config, str(tmp_path)
        )
        assert got == [f"Read {repo}/AGENTS.md"]

    def test_unresolvable_entry_is_dropped_not_emitted_hollow(self):
        """A story must never tell an agent to read a path that isn't there."""
        m = self._lib()
        profile = {
            "research": ["Read {bestPractices}", "Read {targetRepo}/x", "Read the PRD"]
        }
        assert m.build_research(profile, {}) == ["Read the PRD"]

    def test_absent_research_key_yields_nothing(self):
        m = self._lib()
        assert m.build_research({}, {}) == []

    def test_brave_core_research_is_unchanged(self):
        """The line brave-core emitted before the key existed, byte-for-byte."""
        m = self._lib()
        profile = m.load_profile({"project": {"profile": "brave-core"}})
        assert profile["research"] == [
            "Read {bestPractices} to identify which best practice sub-documents "
            "apply, then read those sub-documents"
        ]

    def test_every_profile_uses_only_known_placeholders(self):
        """A typo'd placeholder would ship to an agent verbatim."""
        known = {"{bestPractices}", "{targetRepo}"}
        for name in sorted(os.listdir(PROJECTS_DIR)):
            path = os.path.join(PROJECTS_DIR, name, "profile.json")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                profile = json.load(f)
            for entry in profile.get("research") or []:
                for found in re.findall(r"\{[^}]*\}", entry):
                    assert found in known, (name, found)


class TestSharedDocsCarryNoProjectLabels:
    """Labels belong to the profile. A shared doc that names one hands every
    project brave-core's labels, and `gh pr create --label` fails outright on a
    label the repo does not have -- which is how bravebot PRs lost theirs."""

    BRAVE_CORE_LABELS = (
        "ai-generated",
        "QA/No",
        "CI/skip",
        "release-notes/exclude",
        "OS/Desktop",
        "OS/Android",
        "OS/iOS",
        "disabled-brave-test",
    )

    def test_no_shared_doc_names_a_brave_core_label(self):
        for name in sorted(os.listdir(DOCS_DIR)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(DOCS_DIR, name)) as f:
                text = f.read()
            for label in self.BRAVE_CORE_LABELS:
                assert label not in text, f"docs/{name} hard-codes {label!r}"

    def test_the_brave_core_profile_still_documents_them(self):
        """They have to live somewhere -- moving them out of the shared docs
        must not lose them."""
        with open(os.path.join(PROJECTS_DIR, "brave-core", "docs", "labels.md")) as f:
            text = f.read()
        for label in self.BRAVE_CORE_LABELS:
            assert label in text, label


class TestBravebotProfile:
    """bravebot is a Rust workspace, and its checks are the ones its Makefile
    and ci.yml actually define -- not brave-core's, which is what the generic
    hard-coded steps used to give it."""

    @staticmethod
    def _profile():
        sys.path.insert(0, SCRIPT_DIR)
        from lib.load_config import load_profile

        return load_profile({"project": {"profile": "bravebot"}})

    def test_covers_every_ci_enforced_check(self):
        """`make check-all` is check + check-spec + check-npm + check-msrv +
        check-reviewdog. Each has to appear, or a story can pass here and fail
        in CI."""
        blob = " ".join(self._profile()["validations"])
        for target in (
            "make check",
            "make check-spec",
            "make check-npm",
            "make check-msrv",
            "make check-reviewdog",
        ):
            assert target in blob, target

    def test_covers_linux(self):
        """A macOS host never compiles the Linux backend, and clippy gains
        lints between releases."""
        assert "make check-linux" in " ".join(self._profile()["validations"])

    def test_has_no_chromium_assumptions(self):
        blob = json.dumps(self._profile()).lower()
        for term in ("pnpm", "gtest", "chromium", "presubmit", "best_practices"):
            assert term not in blob, f"bravebot profile leaks {term!r}"

    def test_test_steps_are_cargo(self):
        for kind, step in self._profile()["testSteps"].items():
            assert "cargo test" in step, kind


class TestBravebotTriageAxes:
    """bravebot labels every open issue on three axes, and its /triage-issues
    skill is what applies them. The loop reads them to order its backlog, so
    the prefixes have to be in the profile and the meanings have to stay in
    bravebot's own docs rather than being restated here."""

    AXIS_LABELS = ("importance/p1", "urgency/p1", "size/1")

    @staticmethod
    def _profile():
        sys.path.insert(0, SCRIPT_DIR)
        from lib.load_config import load_profile

        return load_profile({"project": {"profile": "bravebot"}})

    def test_profile_spells_all_three_axes(self):
        sys.path.insert(0, SCRIPT_DIR)
        from lib import triage

        prefixes = triage.axis_prefixes(self._profile())
        assert sorted(prefixes) == sorted(triage.AXES)

    def test_the_prefixes_read_the_labels_bravebot_applies(self):
        sys.path.insert(0, SCRIPT_DIR)
        from lib import triage

        prefixes = triage.axis_prefixes(self._profile())
        got = triage.read_labels(["importance/p2", "urgency/p3", "size/2"], prefixes)
        assert got == {"importance": 2, "urgency": 3, "size": 2}

    def test_no_shared_doc_names_an_axis_label(self):
        """Same rule as every other project label: a shared doc that spells one
        hands it to every project, including the ones that have no such label."""
        for name in sorted(os.listdir(DOCS_DIR)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(DOCS_DIR, name)) as f:
                content = f.read()
            for label in self.AXIS_LABELS:
                assert label not in content, f"docs/{name} hard-codes {label!r}"

    def test_the_profile_docs_name_them(self):
        """They have to live somewhere, and this is where an agent looks."""
        with open(os.path.join(PROJECTS_DIR, "bravebot", "docs", "labels.md")) as f:
            content = f.read()
        for label in self.AXIS_LABELS:
            assert label in content, label

    def test_the_meanings_are_deferred_to_the_target_repo(self):
        """Restating a scale that lives in bravebot's docs/development.md is how
        the two drift apart, and the loop is not the one that defines it."""
        with open(os.path.join(PROJECTS_DIR, "bravebot", "docs", "labels.md")) as f:
            content = f.read()
        assert "docs/development.md" in content


class TestBravebotWorktrees:
    """bravebot stories work in a per-issue worktree, never in the checkout
    run.sh owns -- that checkout is stashed and put back on the default branch
    when a run ends, so a story left in it loses its work."""

    BRAVEBOT_DOCS = os.path.join(PROJECTS_DIR, "bravebot", "docs")

    @staticmethod
    def _profile():
        sys.path.insert(0, SCRIPT_DIR)
        from lib.load_config import load_profile

        return load_profile({"project": {"profile": "bravebot"}})

    def _repo_doc(self):
        with open(os.path.join(self.BRAVEBOT_DOCS, "repo.md")) as f:
            return f.read()

    def test_the_first_story_step_is_entering_the_worktree(self):
        """Every later step reads a path; if the worktree step is not first,
        they read the wrong tree."""
        first = self._profile()["research"][0]
        assert "worktree" in first
        assert "{targetRepo}-" in first

    def test_the_worktree_step_survives_substitution(self, tmp_path):
        """A research entry whose placeholder does not resolve is dropped
        outright -- silently, and with it the whole rule."""
        sys.path.insert(0, SCRIPT_DIR)
        from lib.load_config import build_research

        repo = tmp_path / "bravebot"
        (repo / ".git").mkdir(parents=True)
        got = build_research(
            self._profile(), {"project": {"targetRepoPath": "bravebot"}}, str(tmp_path)
        )
        assert got, "worktree step was dropped"
        assert f"{repo}-" in got[0]

    def test_repo_doc_gives_the_path_scheme(self):
        doc = self._repo_doc()
        assert "../bravebot-<issue-number>" in doc

    def test_the_worktree_is_based_on_upstream_and_not_the_fork(self):
        """`origin` is the bot's fork, and nothing in the story flow pushes
        upstream's commits to it, so a worktree added from `origin/main` starts on
        whatever the fork last held -- a tree old enough to be missing the documents
        the story's own research step tells it to read."""
        doc = self._repo_doc()
        assert 'worktree add -b <branch-name> "$WORK" upstream/main' in doc
        assert 'worktree add -b <branch-name> "$WORK" origin/main' not in doc, (
            "a new story branch is based on the fork"
        )
        assert "fetch upstream" in doc, "nothing makes the base current"

    def test_repo_doc_covers_create_reuse_and_removal(self):
        """A story spans iterations: the second one must re-enter the worktree
        it already has rather than add a second, and merged stories must not
        leave the tree behind."""
        doc = self._repo_doc()
        assert "worktree list" in doc, "no way to detect an existing worktree"
        assert "worktree add -b" in doc, "no way to start one"
        assert "worktree add --track -b" in doc, "no way to reuse a pushed branch"
        assert "worktree remove" in doc, "no teardown"

    def test_shared_docs_point_at_the_rule_wherever_they_name_the_checkout(self):
        """The rule only takes effect if the doc the agent is following at the
        moment it cds sends it to the profile."""
        for name in sorted(os.listdir(DOCS_DIR)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(DOCS_DIR, name)) as f:
                text = f.read()
            if "[targetRepoPath from bot config]" not in text:
                continue
            assert "worktree" in text, f"docs/{name} names the checkout with no pointer"

    # The agent named bravebot is not the project named bravebot: any profile may
    # run it, so the lines that document choosing an agent are allowed to say so.
    AGENT_SELECTION = ("bot.agent", "--agent", "BOT_AGENT")

    # Docs about the agent CLIs themselves name them throughout: a comparison run
    # exists to judge one tool against another, and reading bravebot's own session
    # store is half of that job. Neutrality is about the project named bravebot.
    AGENT_DOCS = ("comparison-runs.md", "comparison-evaluation.md")

    def test_the_pointers_stay_project_neutral(self):
        """Shared docs serve every profile; brave-core has no worktrees."""
        for name in sorted(os.listdir(DOCS_DIR)):
            if not name.endswith(".md") or name in self.AGENT_DOCS:
                continue
            with open(os.path.join(DOCS_DIR, name)) as f:
                text = f.read()
            for line in text.splitlines():
                if any(marker in line for marker in self.AGENT_SELECTION):
                    continue
                assert "bravebot" not in line, f"docs/{name} hard-codes bravebot"


class TestStoryBranchesComeFromUpstream:
    """Where `project.useFork` is true -- the default -- `origin` is the bot's own
    fork and nothing in the flow pushes upstream's commits to it. A story branch
    based on the fork's default branch starts on an old tree and carries commits the
    bot did not write into its own pull request."""

    FORK_BASES = (
        "git pull origin master",
        "git pull origin main",
        "rebase origin/master",
        "rebase origin/main",
        "origin/master\n",
        "origin/main\n",
    )

    @staticmethod
    def _no_fork_profiles():
        """Profiles a shipped config puts on the no-fork layout.

        The rule below is about a fork being stale, so it does not hold where
        there is no fork: `origin` is the pull-request repository itself, and
        setup adds no `upstream` remote to base on instead."""
        names = set()
        for entry in _reference_configs():
            with open(os.path.join(REPO_ROOT, entry)) as f:
                project = json.load(f).get("project") or {}
            if project.get("useFork") is False:
                names.add(project.get("profile") or project.get("name"))
        return names

    def _target_repo_docs(self):
        """Every document that tells the agent how to work the target repo: the
        shared workflow docs and each profile's own. The bot directory is not a
        fork, so `learnable-patterns.md`, which branches there, is not in this set."""
        for name in sorted(os.listdir(DOCS_DIR)):
            if name.endswith(".md") and name != "learnable-patterns.md":
                yield os.path.join("docs", name)
        no_fork = self._no_fork_profiles()
        for profile in sorted(os.listdir(PROJECTS_DIR)):
            profile_docs = os.path.join(PROJECTS_DIR, profile, "docs")
            if not os.path.isdir(profile_docs) or profile in no_fork:
                continue
            for name in sorted(os.listdir(profile_docs)):
                if name.endswith(".md"):
                    yield os.path.join("projects", profile, "docs", name)

    def test_no_doc_bases_a_story_branch_on_the_forks_default_branch(self):
        root = os.path.join(os.path.dirname(__file__), os.pardir)
        for relative in self._target_repo_docs():
            with open(os.path.join(root, relative)) as f:
                text = f.read()
            for phrase in self.FORK_BASES:
                assert phrase not in text, f"{relative} bases work on {phrase!r}"


class TestPrdMode:
    """The PRD is either authored (curated) or a cache the bot refreshes from
    GitHub (auto). Defaulting matters: deployments predating the key treat
    their PRD as authored input and must keep doing so."""

    @staticmethod
    def _lib():
        sys.path.insert(0, SCRIPT_DIR)
        import lib.load_config as m

        return m

    def test_absent_key_defaults_to_curated(self):
        assert self._lib().prd_mode({}) == "curated"

    def test_explicit_auto(self):
        assert self._lib().prd_mode({"project": {"prdMode": "auto"}}) == "auto"

    def test_seed_prd_has_no_placeholder_story(self):
        """A seeded placeholder gets picked up as real work on a bot's first run."""
        path = os.path.join(
            os.path.dirname(__file__), os.pardir, "data", "prd.example.json"
        )
        with open(path) as f:
            assert json.load(f)["stories"] == []

    @staticmethod
    def _sync_prd():
        with open(os.path.join(SCRIPT_DIR, "sync-prd.sh")) as f:
            return f.read()

    AUTO_GATE = 'if [ "$BOT_PRD_MODE" = "auto" ]; then'

    def test_curated_prd_is_never_rewritten_from_github(self):
        """A curated PRD is the operator's document. The bot-PR sync rewrites
        the status of stories already in it, so it runs in auto alone."""
        body = self._sync_prd()
        assert body.index(self.AUTO_GATE) < body.index("sync-bot-prs-to-prd.py")

    def test_curated_prd_still_gets_its_backlog(self):
        """The backlog sync only appends issues nobody has tracked yet, and it
        is what the nightly agent session did before this was a script. Behind
        the auto gate, every curated deployment silently stops picking up newly
        assigned work the day its schedules are re-synced."""
        body = self._sync_prd()
        assert body.index("add-backlog-to-prd.py") < body.index(self.AUTO_GATE)

    def test_a_run_leaves_a_curated_prd_alone(self):
        """Appending is the scheduled job's business, at an hour the operator
        chose -- not something a run does to an authored PRD behind them."""
        with open(os.path.join(os.path.dirname(__file__), os.pardir, "run.sh")) as f:
            body = f.read()
        assert body.index(self.AUTO_GATE) < body.index("scripts/sync-prd.sh")

    def test_sync_starts_no_agent(self):
        """The whole point: keeping the PRD current must cost no tokens."""
        for name in (
            "sync-prd.sh",
            "add-backlog-to-prd.py",
            "sync-bot-prs-to-prd.py",
        ):
            with open(os.path.join(SCRIPT_DIR, name)) as f:
                body = f.read()
            assert "CLAUDE_BIN" not in body, name
            assert "claude -p" not in body, name

    def test_cron_backlog_job_starts_no_agent(self):
        """This job used to spend a whole agent session on a deterministic sync.
        The schedules are per-project now, so no project may reintroduce it."""
        scheduled = []
        for name in sorted(os.listdir(PROJECTS_DIR)):
            path = os.path.join(PROJECTS_DIR, name, "schedules.sh")
            if not os.path.isfile(path):
                continue
            with open(path) as f:
                body = f.read()
            assert "/add-backlog-to-prd'" not in body, name
            if "sync-prd.sh" in body:
                assert "add-backlog -- ./scripts/sync-prd.sh" in body, name
                scheduled.append(name)
        assert scheduled, "no project schedules the PRD sync at all"


class TestCronBlocks:
    """sync-schedules.sh replaces this project's crontab block by matching its
    marker exactly. The marker carries the repo's name, and that name changed:
    a block written before the rename matches nothing, so re-running the script
    leaves the old jobs installed and adds a second copy of every one."""

    LIB = os.path.join(SCRIPT_DIR, "lib", "cron-blocks.sh")

    @classmethod
    def _run(cls, snippet, stdin=""):
        return subprocess.run(
            ["bash", "-c", f'source "{cls.LIB}"\n{snippet}'],
            input=stdin,
            capture_output=True,
            text=True,
        ).stdout

    @staticmethod
    def _block(name, project, job):
        return "\n".join(
            [
                f"# === {name} ({project}) scheduled jobs ===",
                job,
                f"# === end {name} ({project}) ===",
            ]
        )

    def _strip(self, crontab, project="brave-core"):
        return self._run(f'bot_strip_cron_blocks "{project}"', crontab)

    def test_the_marker_written_is_the_current_name(self):
        assert self._run('bot_cron_marker "p"').strip() == "brave-dev-loop (p)"

    def test_the_current_block_is_stripped(self):
        out = self._strip(self._block("brave-dev-loop", "brave-core", "0 1 * * * now"))
        assert "now" not in out

    def test_a_block_from_the_former_repo_name_is_stripped_too(self):
        """Left behind, its jobs keep running beside the ones just installed."""
        out = self._strip(self._block("brave-dev-bot", "brave-core", "0 1 * * * old"))
        assert "old" not in out

    def test_both_spellings_go_in_one_pass(self):
        crontab = "\n".join(
            [
                self._block("brave-dev-bot", "brave-core", "0 1 * * * old"),
                self._block("brave-dev-loop", "brave-core", "0 2 * * * now"),
            ]
        )
        out = self._strip(crontab)
        assert "old" not in out and "now" not in out

    def test_another_projects_block_survives(self):
        """One crontab, several deployments -- that is what the project name in
        the marker is for."""
        out = self._strip(self._block("brave-dev-loop", "bravebot", "0 3 * * * theirs"))
        assert "theirs" in out

    def test_entries_the_bot_never_wrote_survive(self):
        crontab = "\n".join(
            [
                "PATH=/usr/bin",
                "0 4 * * * backup",
                self._block("brave-dev-bot", "brave-core", "0 1 * * * old"),
            ]
        )
        out = self._strip(crontab)
        assert "PATH=/usr/bin" in out and "backup" in out and "old" not in out

    def test_sync_schedules_uses_the_shared_stripper(self):
        """The marker spelling and its history belong in one place."""
        with open(os.path.join(SCRIPT_DIR, "sync-schedules.sh")) as f:
            body = f.read()
        assert "bot_strip_cron_blocks" in body
        assert "bot_cron_marker" in body


class TestBotConfigBool:
    """jq's `//` treats false like null, so reading a boolean with it makes a
    `false` setting indistinguishable from an absent one — every caller then
    falls through to its default and the setting silently inverts."""

    @staticmethod
    def _read(tmp_dir, value, reader):
        bot = os.path.join(tmp_dir, "bot")
        os.makedirs(os.path.join(bot, "scripts", "lib"), exist_ok=True)
        with open(os.path.join(SCRIPT_DIR, "lib", "load-config.sh")) as f:
            src = f.read()
        with open(os.path.join(bot, "scripts", "lib", "load-config.sh"), "w") as f:
            f.write(src)
        cfg = {
            "project": {
                "name": "p",
                "org": "o",
                "prRepository": "o/p",
                "issueRepository": "o/p",
            },
            "bot": {"username": "b"},
        }
        if value is not ...:
            cfg["project"]["useFork"] = value
        with open(os.path.join(bot, "config.json"), "w") as f:
            json.dump(cfg, f)
        probe = os.path.join(bot, "probe.sh")
        with open(probe, "w") as f:
            f.write(
                '#!/bin/bash\nsource "$(dirname "$0")/scripts/lib/load-config.sh"\n'
                f"printf '%s' \"$({reader} '.project.useFork')\"\n"
            )
        os.chmod(probe, 0o755)
        return subprocess.run([probe], capture_output=True, text=True).stdout

    def test_false_reads_as_false_not_empty(self, tmp_dir):
        assert self._read(tmp_dir, False, "bot_config_bool") == "false"

    def test_true_reads_as_true(self, tmp_dir):
        assert self._read(tmp_dir, True, "bot_config_bool") == "true"

    def test_absent_reads_as_empty_so_defaults_apply(self, tmp_dir):
        assert self._read(tmp_dir, ..., "bot_config_bool") == ""

    def test_plain_bot_config_still_loses_false(self, tmp_dir):
        """Documents why bot_config must not be used for booleans."""
        assert self._read(tmp_dir, False, "bot_config") == ""


class TestReviewRequestQueue:
    """The queue is GitHub's own: a PR where the bot is a requested reviewer.
    Nothing local records what has been answered, which is what makes the answer
    the same on every machine the loop is deployed to — the bot submitting a
    review is what removes the PR from the queue, and a re-request is what puts
    it back."""

    GATE = os.path.join(SCRIPT_DIR, "check-review-requests.sh")
    JOB = os.path.join(SCRIPT_DIR, "review-requested.sh")

    @staticmethod
    def _configured_bot():
        """Whichever bot lib/load-config.sh resolves here. The shell loader
        reads config.json beside it, so the suite must not assume a name."""
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source {os.path.join(SCRIPT_DIR, 'lib', 'load-config.sh')}"
                ' >/dev/null && printf "%s" "$BOT_USERNAME"',
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    @staticmethod
    def _stubs(tmp_dir, queue="101\n102\n103", gh_rc=0, failing_pr=None):
        """A gh that answers the queue query and a claude that records its
        prompt, both ahead of the real ones on PATH."""
        bindir = os.path.join(tmp_dir, "bin")
        os.makedirs(bindir, exist_ok=True)
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as f:
            f.write(
                "#!/bin/bash\n"
                'echo "$*" >> "$GH_LOG"\n'
                f"[ {gh_rc} -eq 0 ] || {{ echo 'gh: bad credentials' >&2; exit {gh_rc}; }}\n"
                f"printf '%s' '{queue}'\n"
                f"[ -z '{queue}' ] || echo\n"
            )
        os.chmod(gh, 0o755)
        claude = os.path.join(bindir, "claude")
        with open(claude, "w") as f:
            f.write(
                "#!/bin/bash\n"
                'echo "$*" >> "$CLAUDE_LOG"\n'
                + (
                    f'case "$*" in *"#{failing_pr}"*) exit 2 ;; esac\n'
                    if failing_pr
                    else ""
                )
                + "exit 0\n"
            )
        os.chmod(claude, 0o755)
        return bindir

    @staticmethod
    def _hold_pr_lock(tmp_dir, pr):
        """Another run, mid-review of `pr`: a live process holding its lock.

        A separate process and not a lock file written by hand, because the
        lock is the kernel's — the file on disk is just the inode it lives on,
        and its presence says nothing about whether anything is running."""
        lock = os.path.join(REPO_ROOT, ".ignore", f".review-pr-{pr}.lock")
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        proc = subprocess.Popen(
            [
                "bash",
                "-c",
                f"source {os.path.join(SCRIPT_DIR, 'lib', 'lock.sh')}\n"
                f'bot_acquire_lock "{lock}" || exit 1\n'
                "echo held\nexec sleep 300",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout.readline().strip() == "held", "could not take the PR lock"
        return proc

    def _run(self, script, tmp_dir, bindir, env=None):
        logs = {
            "GH_LOG": os.path.join(tmp_dir, "gh.log"),
            "CLAUDE_LOG": os.path.join(tmp_dir, "claude.log"),
        }
        result = subprocess.run(
            [script],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{bindir}:{os.environ['PATH']}",
                **logs,
                **(env or {}),
            },
        )

        def read(path):
            try:
                with open(path) as f:
                    return [line.strip() for line in f if line.strip()]
            except FileNotFoundError:
                return []

        return result, read(logs["GH_LOG"]), read(logs["CLAUDE_LOG"])

    def test_an_empty_queue_stops_at_the_gate(self, tmp_dir):
        """96 polls a day, and all but a few find nothing. The gate runs before
        the git sync and before any agent, so those cost one API call."""
        result, _, _ = self._run(self.GATE, tmp_dir, self._stubs(tmp_dir, queue=""))
        assert result.returncode == 1, result.stdout
        assert "nothing to do" in result.stdout

    def test_a_failed_query_is_not_an_empty_queue_but_stops_all_the_same(self, tmp_dir):
        """An expired token answers every question with silence. Treating that
        as work would start a session to review nothing."""
        result, _, _ = self._run(self.GATE, tmp_dir, self._stubs(tmp_dir, gh_rc=1))
        assert result.returncode == 1
        assert "Could not query review requests" in result.stderr

    def test_a_queued_request_opens_the_gate_and_names_its_prs(self, tmp_dir):
        result, gh_log, _ = self._run(self.GATE, tmp_dir, self._stubs(tmp_dir))
        assert result.returncode == 0, result.stderr
        assert "101 102 103" in result.stdout
        assert f"review-requested:{self._configured_bot()}" in gh_log[0]
        assert "--state open" in gh_log[0]

    def test_the_job_reviews_every_queued_pr_in_its_own_session(self, tmp_dir):
        """A review is most of a context window on its own. One session per PR,
        and the skill fans out to subagents inside each one."""
        result, _, claude_log = self._run(
            self.JOB, tmp_dir, self._stubs(tmp_dir), {"REVIEW_REQUESTED_MAX_PRS": "3"}
        )
        assert result.returncode == 0, result.stderr
        assert len(claude_log) == 3
        for pr, invocation in zip((101, 102, 103), claude_log):
            assert f"-p /review-prs #{pr} open auto" in invocation

    def test_the_session_may_launch_subagents(self, tmp_dir):
        """Without Task the skill's whole file-based pipeline runs in the one
        session it was trying to keep small."""
        _, _, claude_log = self._run(self.JOB, tmp_dir, self._stubs(tmp_dir))
        assert "--allowedTools" in claude_log[0]
        assert "Task" in claude_log[0].split("--allowedTools")[1]

    def test_the_cap_leaves_the_rest_of_the_queue_for_the_next_poll(self, tmp_dir):
        """Five minutes later there is another poll, and it does not wait for
        this one. Reviewing the whole queue in one job is how a backlog turns
        into a job that never ends."""
        result, _, claude_log = self._run(
            self.JOB, tmp_dir, self._stubs(tmp_dir), {"REVIEW_REQUESTED_MAX_PRS": "2"}
        )
        assert result.returncode == 0, result.stderr
        assert len(claude_log) == 2
        assert "#103" in result.stdout

    def test_five_prs_a_run_by_default(self, tmp_dir):
        """The cap and the poll interval together are the answer-within time
        for a request at the back of the queue. Two per run left eight queued
        requests draining for over two hours."""
        result, _, claude_log = self._run(
            self.JOB,
            tmp_dir,
            self._stubs(tmp_dir, queue="\n".join(str(n) for n in range(101, 108))),
        )
        assert result.returncode == 0, result.stderr
        assert len(claude_log) == 5
        assert "#106 #107" in result.stdout

    def test_a_pr_another_run_is_reviewing_is_walked_past(self, tmp_dir):
        """GitHub only drops a PR from the queue once the review is submitted,
        so an overlapping run sees the PR being reviewed right now at the front
        of its own queue. Without the per-PR lock every run would start on the
        same PR and post the same review."""
        held = self._hold_pr_lock(tmp_dir, 101)
        try:
            result, _, claude_log = self._run(
                self.JOB,
                tmp_dir,
                self._stubs(tmp_dir),
                {"REVIEW_REQUESTED_MAX_PRS": "2"},
            )
        finally:
            held.kill()
            held.wait()
        assert result.returncode == 0, result.stderr
        reviewed = [
            pr for pr in (101, 102, 103) if f"#{pr} open auto" in " ".join(claude_log)
        ]
        assert reviewed == [102, 103], claude_log
        assert "skipped #101" in result.stdout

    def test_a_pr_held_by_another_run_does_not_use_up_the_cap(self, tmp_dir):
        """Counting a PR this run never reviewed would make an overlapping run
        shrink the one behind it: two runs, and the second does one review."""
        held = self._hold_pr_lock(tmp_dir, 101)
        try:
            _, _, claude_log = self._run(
                self.JOB,
                tmp_dir,
                self._stubs(tmp_dir),
                {"REVIEW_REQUESTED_MAX_PRS": "2"},
            )
        finally:
            held.kill()
            held.wait()
        assert len(claude_log) == 2, claude_log

    def test_a_skipped_pr_is_not_reported_as_a_failed_review(self, tmp_dir):
        """The subshell signals "someone else has this" with an exit code. A
        code the agent could also return would make every overlap look like a
        broken review and fail the cron job."""
        held = self._hold_pr_lock(tmp_dir, 101)
        try:
            result, _, _ = self._run(
                self.JOB,
                tmp_dir,
                self._stubs(tmp_dir),
                {"REVIEW_REQUESTED_MAX_PRS": "3"},
            )
        finally:
            held.kill()
            held.wait()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "failed" not in result.stderr

    def test_the_pr_lock_is_released_when_the_session_ends(self, tmp_dir):
        """Held for the life of the session and no longer. A lock that outlived
        its session would be indistinguishable from a review in progress, and
        no later poll would ever pick that PR up again — the queue would go
        quiet one PR at a time with nothing in the log to say why."""
        first, _, first_log = self._run(self.JOB, tmp_dir, self._stubs(tmp_dir))
        assert first.returncode == 0, first.stderr
        assert len(first_log) == 3
        os.remove(os.path.join(tmp_dir, "claude.log"))

        again, _, claude_log = self._run(self.JOB, tmp_dir, self._stubs(tmp_dir))
        assert again.returncode == 0, again.stderr
        assert len(claude_log) == 3, "a leaked lock would have skipped every PR"
        assert "skipped" not in again.stdout

    def test_one_failed_review_does_not_take_the_queue_down_with_it(self, tmp_dir):
        result, _, claude_log = self._run(
            self.JOB,
            tmp_dir,
            self._stubs(tmp_dir, failing_pr=102),
            {"REVIEW_REQUESTED_MAX_PRS": "3"},
        )
        assert len(claude_log) == 3
        assert result.returncode != 0
        assert "Review of #102 failed" in result.stderr

    def test_the_job_asks_again_because_another_machine_may_have_answered(
        self, tmp_dir
    ):
        """Between the gate and the job is a git fetch, a checkout and a target
        repo sync. The queue is shared, so it can empty in that window."""
        result, gh_log, claude_log = self._run(
            self.JOB, tmp_dir, self._stubs(tmp_dir, queue="")
        )
        assert gh_log, "the job took the gate's word for it"
        assert claude_log == []
        assert result.returncode == 0
        assert "nothing to do" in result.stdout


class TestRunLocking:
    """A missing flock used to exit 127, which `|| exit 0` reported as "already
    running" — so on a machine without flock every run and every cron job
    exited successfully having done nothing."""

    LOCK_LIB = os.path.join(SCRIPT_DIR, "lib", "lock.sh")

    def _run(self, body, path_prefix=None):
        env = dict(os.environ)
        if path_prefix:
            env["PATH"] = f"{path_prefix}:{env['PATH']}"
        return subprocess.run(
            ["bash", "-c", f"source {self.LOCK_LIB}\n{body}"],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_acquires_and_blocks_a_second_holder(self, tmp_dir):
        lock = os.path.join(tmp_dir, "a.lock")
        r = self._run(
            f'bot_acquire_lock "{lock}"; echo "first=$?"\n'
            f'( source {self.LOCK_LIB}; bot_acquire_lock "{lock}"; echo "second=$?" )'
        )
        assert "first=0" in r.stdout, r.stdout
        assert "second=1" in r.stdout, r.stdout

    def test_release_allows_reacquisition(self, tmp_dir):
        lock = os.path.join(tmp_dir, "b.lock")
        r = self._run(
            f'bot_acquire_lock "{lock}"; bot_release_lock\n'
            f'bot_acquire_lock "{lock}"; echo "again=$?"'
        )
        assert "again=0" in r.stdout, r.stdout

    def test_stale_lock_from_a_dead_holder_is_reclaimed(self, tmp_dir):
        """A process killed before cleanup must not wedge the bot forever."""
        lock = os.path.join(tmp_dir, "c.lock")
        os.makedirs(lock + ".d")
        with open(os.path.join(lock + ".d", "pid"), "w") as f:
            f.write("999999")
        r = self._run(f'bot_acquire_lock "{lock}"; echo "stale=$?"')
        assert "stale=0" in r.stdout, r.stdout + r.stderr

    def test_missing_flock_is_not_reported_as_contention(self, tmp_dir):
        """The actual bug: no flock must still acquire, not claim 'already running'."""
        lock = os.path.join(tmp_dir, "d.lock")
        r = self._run(
            "_bot_have_flock() { return 1; }\n"
            f'bot_acquire_lock "{lock}"; echo "got=$?"'
        )
        assert "got=0" in r.stdout, r.stdout + r.stderr

    def test_flock_path_reports_contention_from_flock(self, tmp_dir):
        """When flock is present its exit status is what decides, unchanged."""
        lock = os.path.join(tmp_dir, "e.lock")
        held = self._run(
            "_bot_have_flock() { return 0; }\nflock() { return 1; }\n"
            f'bot_acquire_lock "{lock}"; echo "held=$?"'
        )
        free = self._run(
            "_bot_have_flock() { return 0; }\nflock() { return 0; }\n"
            f'bot_acquire_lock "{lock}"; echo "free=$?"'
        )
        assert "held=1" in held.stdout, held.stdout + held.stderr
        assert "free=0" in free.stdout, free.stdout + free.stderr

    def test_one_slot_behaves_exactly_like_the_single_lock_it_replaced(self, tmp_dir):
        """Every caller that does not ask for more than one slot has to keep
        contending on the file it always used, or a deployment mid-upgrade runs
        two of a job that may only ever run once."""
        lock = os.path.join(tmp_dir, "one.lock")
        r = self._run(
            f'bot_acquire_slot_of "{lock}" 1; echo "first=$? file=$BOT_LOCK_FILE"\n'
            f'( source {self.LOCK_LIB}; bot_acquire_lock "{lock}"; echo "second=$?" )'
        )
        assert "first=0" in r.stdout, r.stdout + r.stderr
        assert f"file={lock}" in r.stdout, "slot 1 must be the bare lock file"
        assert "second=1" in r.stdout, r.stdout

    def test_slots_are_handed_out_until_the_count_runs_out(self, tmp_dir):
        lock = os.path.join(tmp_dir, "many.lock")
        r = self._run(
            "\n".join(
                f'( source {self.LOCK_LIB}; bot_acquire_slot_of "{lock}" 3'
                f'; echo "rc=$? slot=$BOT_LOCK_SLOT"; exec sleep 30 ) &'
                for _ in range(3)
            )
            + "\nsleep 1\n"
            f'bot_acquire_slot_of "{lock}" 3; echo "fourth=$?"\n'
            "kill $(jobs -p) 2>/dev/null; wait 2>/dev/null"
        )
        assert sorted(re.findall(r"rc=0 slot=(\d)", r.stdout)) == ["1", "2", "3"], (
            r.stdout
        )
        assert "fourth=1" in r.stdout, r.stdout

    def test_a_freed_slot_is_handed_out_again(self, tmp_dir):
        lock = os.path.join(tmp_dir, "reuse.lock")
        r = self._run(
            f'bot_acquire_slot_of "{lock}" 2; echo "a=$BOT_LOCK_SLOT"\n'
            "bot_release_lock\n"
            f'bot_acquire_slot_of "{lock}" 2; echo "b=$BOT_LOCK_SLOT"'
        )
        assert "a=1" in r.stdout and "b=1" in r.stdout, r.stdout

    def test_a_bad_slot_count_is_an_error_not_a_silent_single_slot(self, tmp_dir):
        """`--slots` comes from a schedule file. A typo that quietly meant one
        would serialize a job that was meant to overlap, with nothing to see."""
        lock = os.path.join(tmp_dir, "bad.lock")
        for count in ("0", "two", "-1"):
            r = self._run(f'bot_acquire_slot_of "{lock}" "{count}"; echo "rc=$?"')
            assert "rc=2" in r.stdout, f"count {count!r}: {r.stdout}"

    def test_no_count_means_one_slot(self, tmp_dir):
        """An omitted count is the single-instance case, not a bad argument."""
        lock = os.path.join(tmp_dir, "default.lock")
        r = self._run(
            f'bot_acquire_slot_of "{lock}"; echo "rc=$? slot=$BOT_LOCK_SLOT"\n'
            f'( source {self.LOCK_LIB}; bot_acquire_slot_of "{lock}"; echo "second=$?" )'
        )
        assert "rc=0 slot=1" in r.stdout, r.stdout + r.stderr
        assert "second=1" in r.stdout, r.stdout

    def test_no_call_site_still_uses_bare_flock(self):
        # run.sh takes a numbered run slot; with-lock.sh takes a named lock.
        # Either way the lock library owns the flock, not the call site.
        entry_points = {
            "../run.sh": "bot_acquire_run_slot",
            "with-lock.sh": "bot_acquire_slot_of",
        }
        for name, entry in entry_points.items():
            with open(os.path.join(SCRIPT_DIR, name)) as f:
                body = f.read()
            assert "flock -n 200" not in body, name
            assert entry in body, name


class TestProfileMismatch:
    """A config that still says `default` while projects/<project.name>/ exists
    is one where nobody chose a profile. Nothing about it fails: stories just
    get generic validations and a docs pointer into a directory that is not
    there, iteration after iteration, until someone reads the prompt closely.
    Both resolvers must catch it, and they must agree."""

    @staticmethod
    def _layout(root, project="bravebot", profile=..., has_profile_dir=True):
        """A bot dir with load-config.sh, a config, and maybe a project profile."""
        bot = os.path.join(root, "bot")
        os.makedirs(os.path.join(bot, "scripts", "lib"), exist_ok=True)
        with open(os.path.join(SCRIPT_DIR, "lib", "load-config.sh")) as f:
            src = f.read()
        with open(os.path.join(bot, "scripts", "lib", "load-config.sh"), "w") as f:
            f.write(src)
        if has_profile_dir:
            profile_dir = os.path.join(bot, "projects", project)
            os.makedirs(profile_dir, exist_ok=True)
            with open(os.path.join(profile_dir, "profile.json"), "w") as f:
                json.dump({"validations": []}, f)
        cfg = {
            "project": {
                "name": project,
                "org": "o",
                "prRepository": f"o/{project}",
                "issueRepository": f"o/{project}",
            },
            "bot": {"username": "b"},
        }
        if profile is not ...:
            cfg["project"]["profile"] = profile
        with open(os.path.join(bot, "config.json"), "w") as f:
            json.dump(cfg, f)
        return bot, cfg

    @staticmethod
    def _py(cfg, bot):
        sys.path.insert(0, SCRIPT_DIR)
        from lib.load_config import profile_mismatch

        return profile_mismatch(cfg, bot)

    @staticmethod
    def _sh(bot):
        """The message bash reports, or None when it reports none."""
        probe = os.path.join(bot, "probe.sh")
        with open(probe, "w") as f:
            f.write(
                '#!/bin/bash\nsource "$(dirname "$0")/scripts/lib/load-config.sh"\n'
                "bot_profile_mismatch || exit 7\n"
            )
        os.chmod(probe, 0o755)
        result = subprocess.run([probe], capture_output=True, text=True)
        if result.returncode == 7:
            return None
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def test_wizard_default_beside_a_project_profile_is_reported(self, tmp_dir):
        bot, cfg = self._layout(tmp_dir, profile="default")
        for message in (self._py(cfg, bot), self._sh(bot)):
            assert message, "a profile nobody chose must be reported"
            assert '"profile": "bravebot"' in message, message

    def test_absent_profile_beside_a_project_profile_is_reported(self, tmp_dir):
        """Absent resolves to brave-core, which is just as wrong for bravebot."""
        bot, cfg = self._layout(tmp_dir)
        for message in (self._py(cfg, bot), self._sh(bot)):
            assert message, "an unset profile must be reported"
            assert "brave-core" in message, message
            assert '"profile": "bravebot"' in message, message

    def test_matching_profile_is_silent(self, tmp_dir):
        bot, cfg = self._layout(tmp_dir, profile="bravebot")
        assert self._py(cfg, bot) is None
        assert self._sh(bot) is None

    def test_a_deliberately_different_profile_is_silent(self, tmp_dir):
        """Naming another profile is a choice; only the untouched value is a bug."""
        bot, cfg = self._layout(tmp_dir, profile="brave-core")
        assert self._py(cfg, bot) is None
        assert self._sh(bot) is None

    def test_default_with_no_project_profile_is_silent(self, tmp_dir):
        """The generic profile is the right answer for a project without one."""
        bot, cfg = self._layout(tmp_dir, profile="default", has_profile_dir=False)
        assert self._py(cfg, bot) is None
        assert self._sh(bot) is None

    def test_shipped_config_examples_name_their_own_profile(self):
        """The templates operators copy must not seed the bug."""
        for name in _reference_configs():
            with open(os.path.join(REPO_ROOT, name)) as f:
                cfg = json.load(f)["project"]
            named = os.path.join(PROJECTS_DIR, cfg["name"], "profile.json")
            if os.path.exists(named):
                assert cfg.get("profile") == cfg["name"], name

    def test_run_sh_refuses_to_start_on_it(self):
        """A warning would be read as often as the last four iterations were."""
        with open(os.path.join(SCRIPT_DIR, os.pardir, "run.sh")) as f:
            body = f.read()
        assert "bot_profile_mismatch" in body
        assert re.search(r"bot_profile_mismatch\)[\s\S]{0,120}?exit 1", body), body

    def test_prd_writers_refuse_to_write_from_it(self):
        """Acceptance criteria written from the wrong profile outlive the config."""
        for name in ("add-backlog-to-prd.py", "sync-bot-prs-to-prd.py"):
            with open(os.path.join(SCRIPT_DIR, name)) as f:
                body = f.read()
            assert "require_matching_profile(_config, _bot_dir)" in body, name

    def test_setup_carries_the_profile_forward(self):
        """Re-running the wizard for one unrelated answer used to reset it."""
        with open(os.path.join(SCRIPT_DIR, "setup.sh")) as f:
            body = f.read()
        assert "PREV_PROFILE=$(_prev '.project.profile')" in body
        assert 'DEFAULT_CFG_PROFILE="$PREV_PROFILE"' in body

    def test_prompt_omits_a_docs_dir_the_profile_does_not_have(self):
        """projects/default/ ships no docs; the prompt must not claim it does."""
        with open(os.path.join(SCRIPT_DIR, os.pardir, "run.sh")) as f:
            body = f.read()
        assert '-d "$BOT_PROFILE_DIR/docs"' in body


class TestAgentSelection:
    """An agent the loop will select but has no branch for runs another agent's
    command line instead of its own: the launcher falls through to Claude, and the
    resume note fell through to Codex. Neither fails — the run just uses the wrong
    binary and prints the wrong advice. So every name the validation accepts must
    appear at each point that switches on the agent."""

    RUN_SH = os.path.join(SCRIPT_DIR, os.pardir, "run.sh")
    LOAD_CONFIG = os.path.join(SCRIPT_DIR, "lib", "load-config.sh")

    @classmethod
    def _body(cls):
        with open(cls.RUN_SH) as f:
            return f.read()

    @classmethod
    def _accepted(cls):
        """The agent names `case "$BOT_AGENT"` lets through."""
        match = re.search(r"^\s*((?:[a-z]+\|)+[a-z]+)\)\s*;;", cls._body(), re.M)
        assert match, "no agent validation case in run.sh"
        return match.group(1).split("|")

    @classmethod
    def _region(cls, start, end):
        body = cls._body()
        first = body.index(start)
        return body[first : body.index(end, first)]

    def test_bravebot_is_accepted(self):
        assert "bravebot" in self._accepted()

    def test_every_accepted_agent_has_a_launch_branch(self):
        launch = self._region(
            "# Run the agent from the bot directory", "  stop_title_watch"
        )
        for agent in self._accepted():
            if agent == "claude":
                continue  # the else branch, reached by whatever is left
            assert f'[ "$BOT_AGENT" = "{agent}" ]' in launch, agent

    def test_every_accepted_agent_is_checked_for_completion(self):
        check = self._region("COMPLETION_CHECK=0", 'if [ "$COMPLETION_CHECK" -gt 0 ]')
        for agent in self._accepted():
            if agent == "claude":
                continue
            assert f'[ "$BOT_AGENT" = "{agent}" ]' in check, agent

    def test_every_accepted_agent_names_itself_at_startup(self):
        banner = self._region("Run slot $BOT_RUN_SLOT", "Logs will be saved to")
        for agent in self._accepted():
            if agent == "claude":
                continue
            assert f'[ "$BOT_AGENT" = "{agent}" ]' in banner, agent

    def test_model_and_binary_flags_route_to_every_accepted_agent(self):
        """--model or --agent-bin silently doing nothing looks like the agent
        ignoring the flag, which is unfalsifiable from the outside."""
        routing = self._region('if [ -n "$CLI_MODEL" ]', "PRD_FILE=")
        for agent in self._accepted():
            assert f"BOT_{agent.upper()}_MODEL=" in routing, agent
            assert f"BOT_{agent.upper()}_BIN=" in routing, agent

    def test_every_refusal_lists_what_is_accepted(self):
        """A refusal that names no alternative leaves the operator guessing which
        spelling was wrong."""
        listed = " | ".join(self._accepted())
        refusals = [
            line
            for line in self._body().splitlines()
            if re.search(
                r"unsupported (comparison )?agent|--(comparison-)?agent requires", line
            )
        ]
        assert len(refusals) >= 2, refusals
        for line in refusals:
            assert f"expected: {listed}" in line, line

    @staticmethod
    def _bravebot_bin(tmp_dir, env=None):
        """$BOT_BRAVEBOT_BIN as load-config.sh resolves it, with no config keys."""
        bot = os.path.join(tmp_dir, "bot")
        os.makedirs(os.path.join(bot, "scripts", "lib"), exist_ok=True)
        with open(TestAgentSelection.LOAD_CONFIG) as f:
            src = f.read()
        with open(os.path.join(bot, "scripts", "lib", "load-config.sh"), "w") as f:
            f.write(src)
        with open(os.path.join(bot, "config.json"), "w") as f:
            json.dump(
                {
                    "project": {
                        "name": "p",
                        "org": "o",
                        "prRepository": "o/p",
                        "issueRepository": "o/p",
                    },
                    "bot": {"username": "b", "agent": "bravebot"},
                },
                f,
            )
        probe = os.path.join(bot, "probe.sh")
        with open(probe, "w") as f:
            f.write(
                '#!/bin/bash\nsource "$(dirname "$0")/scripts/lib/load-config.sh"\n'
                "printf '%s' \"$BOT_BRAVEBOT_BIN\"\n"
            )
        os.chmod(probe, 0o755)
        return subprocess.run(
            [probe], capture_output=True, text=True, env={**os.environ, **(env or {})}
        ).stdout

    def test_binary_resolves_with_nothing_configured(self, tmp_dir):
        assert os.path.basename(self._bravebot_bin(tmp_dir)) == "bravebot"

    def test_binary_from_the_environment_is_kept(self, tmp_dir):
        """There is no config key to fall back on, so the environment is the only
        way to pin a binary for a scheduled run rather than a typed one."""
        chosen = os.path.join(tmp_dir, "target", "release", "bravebot")
        assert self._bravebot_bin(tmp_dir, {"BOT_BRAVEBOT_BIN": chosen}) == chosen


# ── scripts/wait-gate.sh ─────────────────────────────────────────────────────

WAIT_GATE = os.path.join(SCRIPT_DIR, "wait-gate.sh")


def run_wait_gate(*args, cwd=None):
    # /bin/bash, not PATH bash: production runs macOS's bash 3.2.
    return subprocess.run(
        ["/bin/bash", WAIT_GATE, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


class TestWaitGateVerdicts:
    def test_reports_the_gates_own_exit_code(self, tmp_dir):
        """The gate's status has to come out of its log. Read from the pipeline
        that printed it instead, and a failing gate reports as passing -- the
        false-pass this script exists to prevent."""
        out = run_wait_gate(
            "run", "--timeout", "20", "--log-dir", tmp_dir, "echo boom; exit 2"
        )
        assert out.returncode == 1
        assert "FAIL exit=2" in out.stdout

    def test_prints_the_failing_lines_with_the_verdict(self, tmp_dir):
        """So reading the verdict does not cost a second turn to grep the log."""
        out = run_wait_gate(
            "run",
            "--timeout",
            "20",
            "--log-dir",
            tmp_dir,
            "echo 'error: mismatched types'; exit 101",
        )
        assert "error: mismatched types" in out.stdout

    def test_a_signalled_gate_is_not_a_pass(self, tmp_dir):
        """A gate killed before it wrote a status wrote no status. Treating the
        missing line as zero is how a killed check gets reported as evidence."""
        started = run_wait_gate("start", "--log-dir", tmp_dir, "sleep 30")
        log_dir = started.stdout.strip()
        with open(os.path.join(log_dir, "1.pid")) as f:
            os.kill(int(f.read().strip()), 9)
        out = run_wait_gate("wait", "--timeout", "10", log_dir)
        assert out.returncode == 1
        assert "KILLED" in out.stdout

    def test_runs_the_gate_in_the_given_directory(self, tmp_dir):
        """Gates run in the story's worktree, never where the session stands."""
        work = os.path.join(tmp_dir, "work")
        os.makedirs(work)
        logs = os.path.join(tmp_dir, "logs")
        out = run_wait_gate(
            "run", "--dir", work, "--timeout", "20", "--log-dir", logs, "pwd"
        )
        assert out.returncode == 0
        with open(os.path.join(logs, "1.log")) as f:
            assert os.path.realpath(f.readline().strip()) == os.path.realpath(work)

    def test_rejects_a_timeout_that_is_not_seconds(self):
        assert run_wait_gate("run", "--timeout", "5m", "true").returncode == 1


class TestWaitGateStart:
    def test_start_returns_before_the_gate_finishes(self, tmp_dir):
        """`start` exists so a review can run while the gate does. Bash keeps a
        command substitution open until every writer of the inherited stdout is
        gone, so a gate holding that descriptor makes `start` block for the full
        gate -- which reads as working, just never overlapping anything."""
        out = run_wait_gate("start", "--log-dir", tmp_dir, "sleep 6")
        assert out.returncode == 0
        assert out.stdout.strip() == tmp_dir
        # The gate stamps .end when it finishes; absent means still running.
        assert not os.path.exists(os.path.join(tmp_dir, "1.end"))

    def test_gates_in_one_invocation_overlap(self, tmp_dir):
        """Two gates given together run at once, so the wait costs the slowest
        rather than the sum. Asserted on the gates' own clocks: a wall-time
        bound flakes on a machine already running fifteen slots."""
        marker = os.path.join(tmp_dir, "second-began")
        run_wait_gate(
            "run",
            "--timeout",
            "30",
            "--log-dir",
            tmp_dir,
            "sleep 3; date +%s > " + os.path.join(tmp_dir, "first-ended"),
            "date +%s > " + marker,
        )
        with open(marker) as f:
            second_began = int(f.read().strip())
        with open(os.path.join(tmp_dir, "first-ended")) as f:
            first_ended = int(f.read().strip())
        assert second_began <= first_ended

    def test_wait_reports_a_gate_another_process_started(self, tmp_dir):
        """The overlap is two tool calls, so the verdict has to survive the
        process that launched the gate exiting."""
        log_dir = run_wait_gate("start", "--log-dir", tmp_dir, "sleep 1").stdout.strip()
        out = run_wait_gate("wait", "--timeout", "20", log_dir)
        assert out.returncode == 0
        assert "PASS" in out.stdout

    def test_a_finished_gate_reports_its_own_duration(self, tmp_dir):
        """Not the time until something got round to asking, which is what a
        gate waited on after the fact would otherwise report."""
        log_dir = run_wait_gate("start", "--log-dir", tmp_dir, "true").stdout.strip()
        time.sleep(3)
        out = run_wait_gate("wait", "--timeout", "10", log_dir)
        assert "0m00s" in out.stdout


class TestWaitGateTimeout:
    def test_timeout_is_distinct_from_failure_and_leaves_the_gate_running(
        self, tmp_dir
    ):
        """Exit 2, not 1: a gate that has not answered yet has not failed, and
        killing it would throw away a build that is minutes from done."""
        out = run_wait_gate("run", "--timeout", "1", "--log-dir", tmp_dir, "sleep 20")
        assert out.returncode == 2
        assert "RUNNING" in out.stdout
        assert "wait-gate.sh wait" in out.stdout
        with open(os.path.join(tmp_dir, "1.pid")) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # raises if the timeout took the gate with it
        os.kill(pid, 9)

    def test_rejects_a_directory_it_did_not_write(self, tmp_dir):
        assert run_wait_gate("wait", tmp_dir).returncode == 1


class TestProjectSchedules:
    """The crontab block is built by sync-schedules.sh, but the jobs in it come
    from projects/<profile>/schedules.sh — one file per project, so a machine
    running two of them installs two independent sets of jobs."""

    GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "brave-core-cron.txt")

    @staticmethod
    def _bot_dir(tmp_dir, profile, name=None):
        """A bot directory holding the real scripts and profiles, configured
        for one project. sync-schedules.sh reads the config beside it, so the
        suite must not render against whatever this machine has installed."""
        import shutil

        bot = os.path.join(tmp_dir, "bot")
        os.makedirs(bot, exist_ok=True)
        for d in ("scripts", "projects"):
            dest = os.path.join(bot, d)
            if not os.path.isdir(dest):
                shutil.copytree(
                    os.path.join(os.path.dirname(__file__), os.pardir, d),
                    dest,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
        config = {
            "project": {
                "name": name or profile,
                "org": "o",
                "prRepository": "o/p",
                "issueRepository": "o/p",
                "defaultBranch": "master",
                "profile": profile,
            },
            "bot": {"username": "b", "claudeBin": "/usr/bin/claude"},
        }
        with open(os.path.join(bot, "config.json"), "w") as f:
            json.dump(config, f)
        return bot

    @classmethod
    def _render(cls, tmp_dir, profile, name=None):
        """The block sync-schedules.sh would install, with the bot directory
        replaced by a placeholder so the text does not depend on where it ran."""
        bot = cls._bot_dir(tmp_dir, profile, name)
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.replace(os.path.realpath(bot), "{BOT}").replace(
            bot, "{BOT}"
        )

    @staticmethod
    def _jobs(block):
        return [
            line
            for line in block.splitlines()
            if re.match(r"^[0-9*]", line)  # a cron line, not a comment or SHELL=
        ]

    @classmethod
    def _run_jobs(cls, block):
        """The jobs that start a run.sh. The hour-by-hour reasoning below is
        about those: they hold a run slot for hours. The gated polls run on
        every hour by design and are constrained on minutes instead."""
        return [j for j in cls._jobs(block) if "./run.sh " in j]

    @classmethod
    def _review_request_job(cls, tmp_dir, profile):
        (job,) = [
            j
            for j in cls._jobs(cls._render(tmp_dir, profile))
            if "review-requested.sh" in j
        ]
        return job

    def test_brave_core_jobs_are_what_is_installed_today(self, tmp_dir):
        """These jobs run unattended on a machine nobody watches. Moving them
        into a profile is a refactor, and a refactor that changes one cron line
        is a schedule change that no one asked for."""
        with open(self.GOLDEN) as f:
            assert self._render(tmp_dir, "brave-core") == f.read()

    def test_bravebot_runs_three_agent_runs_a_day(self, tmp_dir):
        assert len(self._run_jobs(self._render(tmp_dir, "bravebot"))) == 3

    def test_bravebot_runs_thirty_iterations_overnight_and_ten_twice_after(
        self, tmp_dir
    ):
        jobs = self._jobs(self._render(tmp_dir, "bravebot"))
        (overnight,) = [j for j in jobs if j.startswith("0 1 * * * ")]
        (midday,) = [j for j in jobs if j.startswith("45 12 * * * ")]
        (evening,) = [j for j in jobs if j.startswith("0 17 * * * ")]
        assert "./run.sh 30 " in overnight
        assert "./run.sh 10 " in midday
        assert "./run.sh 10 " in evening

    def test_no_bravebot_run_is_alive_when_the_overnight_one_starts(self, tmp_dir):
        """Runs overlap on purpose, each on its own slot, but a wedged one must
        not accumulate: a run still going when 01:00 comes round again has
        outlived every story it claimed, so every job's timeout-tree.sh cap has
        to expire before that hour."""
        day = 24 * 60 * 60
        for job in self._run_jobs(self._render(tmp_dir, "bravebot")):
            minute, hour = (int(field) for field in job.split()[:2])
            # How long this job has until 01:00 next comes round. The overnight
            # job starts on it, so the wrap gives it nothing and it gets the day.
            until_overnight = (1 * 60 * 60 - (hour * 60 * 60 + minute * 60)) % day
            m = re.search(r"timeout-tree\.sh (\d+) \./run\.sh", job)
            assert m, job
            assert int(m.group(1)) < (until_overnight or day), job

    def test_bravebot_does_not_share_an_hour_with_brave_core(self, tmp_dir):
        """Both projects can be deployed on one machine, and each run.sh drives
        its own agent session for hours."""
        mine = {j.split()[1] for j in self._run_jobs(self._render(tmp_dir, "bravebot"))}
        theirs = {
            j.split()[1] for j in self._run_jobs(self._render(tmp_dir, "brave-core"))
        }
        assert not (mine & theirs)

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_a_review_requested_of_the_bot_is_polled_for(self, tmp_dir, profile):
        """Someone clicking "Request review" (or the re-request arrow) is an ask
        no schedule predicted. The poll is what turns it into a review, and it
        goes through the gate so the 96 polls a day that find nothing cost
        nothing — no agent session appears in the crontab line at all."""
        job = self._review_request_job(tmp_dir, profile)
        assert "./scripts/check-review-requests.sh && git fetch origin" in job
        assert "-- ./scripts/review-requested.sh" in job
        assert "/usr/bin/claude" not in job

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_the_poll_runs_every_five_minutes(self, tmp_dir, profile):
        """The answer-within promise is the poll interval plus however long the
        queue ahead of a request takes. Fifteen minutes of that was the poll."""
        job = self._review_request_job(tmp_dir, profile)
        minutes = [int(m) for m in job.split()[0].split(",")]
        assert len(minutes) == 12
        assert all(b - a == 5 for a, b in zip(minutes, minutes[1:]))

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_the_poll_does_not_wait_for_the_previous_poll(self, tmp_dir, profile):
        """A review runs for far longer than five minutes. A single exclusive
        lock would make every poll in between exit on it, so the interval would
        buy nothing at all — the queue would still drain one session at a time.
        The cap moves to a slot count, and review-requested.sh locks per PR."""
        job = self._review_request_job(tmp_dir, profile)
        assert "./scripts/with-lock.sh review-prs --slots 3 " in job

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot"])
    def test_a_poll_outlives_five_reviews_before_it_is_killed(self, tmp_dir, profile):
        """with-lock.sh kills the job at its timeout. Five reviews in one run
        do not fit in the two-hour default, and a run killed partway leaves the
        PRs it had not reached for the next poll — forever, if every run dies
        at the same point."""
        job = self._review_request_job(tmp_dir, profile)
        timeout = int(re.search(r"--timeout (\d+)", job).group(1))
        assert timeout >= 5 * 30 * 60

    def test_the_two_projects_poll_on_different_minutes(self, tmp_dir):
        """Deployed on one machine these are two checkouts, two crontab blocks
        and two locks — nothing stops them polling at once, and the job behind
        the gate builds a worktree per PR."""

        def minutes(profile):
            return set(self._review_request_job(tmp_dir, profile).split()[0].split(","))

        assert not (minutes("brave-core") & minutes("bravebot"))

    def test_the_poll_and_the_sweep_draw_on_the_same_slots(self, tmp_dir):
        """A PR review is a full checkout of the target repo per PR, so the
        total number running at once has to be bounded however they were
        started. One lock name, and — this is the part that bites — the same
        count from every job that uses it: a job asking for one slot takes slot
        1 only, and exits doing nothing whenever a job asking for three holds
        it. That is a sweep starved by the poll."""
        jobs = self._jobs(self._render(tmp_dir, "brave-core"))
        reviewing = [j for j in jobs if "review-prs" in j]
        assert len(reviewing) == 3  # weekday sweep, weekend sweep, the poll
        counts = {
            re.search(r"with-lock\.sh review-prs --slots (\d+)", j).group(1)
            for j in reviewing
        }
        assert counts == {"3"}

    def test_bravebot_reviews_only_what_it_is_asked_to(self, tmp_dir):
        """This project has no automated best-practices sweep yet. Answering an
        explicit request does not wait on one: what a human asked for is not the
        unsolicited pass over everything that moved today."""
        block = self._render(tmp_dir, "bravebot")
        assert "review-requested.sh" in block
        assert "/review-prs 1d" not in block

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot", "default"])
    def test_every_job_runs_in_the_bot_dir_and_logs_there(self, tmp_dir, profile):
        for job in self._jobs(self._render(tmp_dir, profile)):
            assert " cd {BOT} && source .envrc" in job
            assert job.endswith("-cron.log 2>&1")

    @pytest.mark.parametrize("profile", ["brave-core", "bravebot", "default"])
    def test_every_job_resets_the_bot_repo_first(self, tmp_dir, profile):
        """A job that runs the checkout as it was left cannot pick up a fix."""
        for job in self._jobs(self._render(tmp_dir, profile)):
            assert "git reset --hard origin/master" in job

    def test_a_profile_with_no_schedules_file_gets_the_default_ones(self, tmp_dir):
        """Adding a project is still just a profile directory."""
        bot = self._bot_dir(tmp_dir, "brave-core")
        os.remove(os.path.join(bot, "projects", "brave-core", "schedules.sh"))
        result = subprocess.run(
            [os.path.join(bot, "scripts", "sync-schedules.sh"), "--print"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "# Jobs: projects/default/schedules.sh" in result.stdout

    def test_the_block_says_which_file_its_jobs_came_from(self, tmp_dir):
        """The operator's next question after reading a crontab line is where
        to change it."""
        block = self._render(tmp_dir, "bravebot")
        assert "# Jobs: projects/bravebot/schedules.sh" in block

    def test_the_block_is_marked_with_the_project_name(self, tmp_dir):
        """What keeps one project's install from stripping another's jobs."""
        block = self._render(tmp_dir, "bravebot", name="bravebot")
        assert block.startswith("# === brave-dev-loop (bravebot) scheduled jobs ===")
        assert block.rstrip().endswith("# === end brave-dev-loop (bravebot) ===")

    def test_printing_touches_no_crontab(self):
        """The suite runs on the machine whose schedules these are."""
        with open(os.path.join(SCRIPT_DIR, "sync-schedules.sh")) as f:
            body = f.read()
        assert body.index("if $PRINT_ONLY; then") < body.index("| crontab -")


class TestPythonFileLock:
    """lib/file_lock.py and lib/repo_lock.py: the shell locks, for Python.

    The review-prs skill is Python and it fetches into the same repository
    run.sh builds worktrees in. Two mechanisms guarding one repository
    serialize nothing, so what matters here is not that the Python lock works
    but that it is the *same* lock."""

    LOCK_LIB = os.path.join(SCRIPT_DIR, "lib", "lock.sh")

    @staticmethod
    def _py(body):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, {SCRIPT_DIR!r})\n{body}",
            ],
            capture_output=True,
            text=True,
        )

    def test_the_repo_lock_file_is_the_one_the_shell_uses(self, tmp_dir):
        """git-repo-lock.sh names the file from a sha1 of the repo's absolute
        path. Python has to spell it identically or the two never contend."""
        repo = os.path.join(tmp_dir, "repo")
        os.makedirs(repo)
        shell = subprocess.run(
            [
                "bash",
                "-c",
                'REPO_ABS="$(cd "$1" && pwd)"; '
                "printf '%s' \"$REPO_ABS\" | shasum | cut -c1-12",
                "bash",
                repo,
            ],
            capture_output=True,
            text=True,
        )
        assert shell.returncode == 0, shell.stderr
        expected = f".git-{shell.stdout.strip()}.lock"

        r = self._py(
            "from lib.repo_lock import lock_path\n"
            f"print(lock_path({repo!r}, bot_dir={tmp_dir!r}))"
        )
        assert r.returncode == 0, r.stderr
        assert os.path.basename(r.stdout.strip()) == expected, r.stdout

    def test_python_and_shell_exclude_each_other(self, tmp_dir):
        """The property the whole thing rests on: a lock the shell holds is a
        lock Python waits for."""
        lock = os.path.join(tmp_dir, "shared.lock")
        holder = subprocess.Popen(
            [
                "bash",
                "-c",
                f"source {self.LOCK_LIB}\n"
                f'bot_acquire_lock "{lock}" || exit 1\n'
                "echo held\nexec sleep 30",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            r = self._py(
                "from lib.file_lock import file_lock, FileLockTimeout\n"
                "try:\n"
                f"    with file_lock({lock!r}, timeout=1):\n"
                "        print('acquired')\n"
                "except FileLockTimeout:\n"
                "    print('waited')\n"
            )
        finally:
            holder.kill()
            holder.wait()
        assert r.returncode == 0, r.stderr
        assert "waited" in r.stdout, r.stdout

    def test_a_released_python_lock_is_free_for_the_shell(self, tmp_dir):
        lock = os.path.join(tmp_dir, "handover.lock")
        r = self._py(
            "from lib.file_lock import file_lock\n"
            f"with file_lock({lock!r}, timeout=1):\n"
            "    pass\n"
            "print('done')\n"
        )
        assert "done" in r.stdout, r.stderr
        shell = subprocess.run(
            [
                "bash",
                "-c",
                f'source {self.LOCK_LIB}\nbot_acquire_lock "{lock}"; echo "rc=$?"',
            ],
            capture_output=True,
            text=True,
        )
        assert "rc=0" in shell.stdout, shell.stdout + shell.stderr

    def test_the_lock_serializes_threads_of_one_process_too(self, tmp_dir):
        """It replaced a threading.Lock, so it has to keep doing that job. A
        lock held on one descriptor shared between threads would not."""
        lock = os.path.join(tmp_dir, "threads.lock")
        r = self._py(
            "import threading\n"
            "from lib.file_lock import file_lock\n"
            "overlaps = []\n"
            "inside = []\n"
            "def work():\n"
            f"    with file_lock({lock!r}, timeout=10):\n"
            "        inside.append(1)\n"
            "        overlaps.append(len(inside))\n"
            "        inside.pop()\n"
            "ts = [threading.Thread(target=work) for _ in range(8)]\n"
            "[t.start() for t in ts]\n"
            "[t.join() for t in ts]\n"
            "print('max', max(overlaps), 'runs', len(overlaps))\n"
        )
        assert r.returncode == 0, r.stderr
        assert "max 1 runs 8" in r.stdout, r.stdout

    def test_a_concurrent_json_update_keeps_both_writers_entries(self, tmp_dir):
        """The review cache, written by two runs finishing moments apart. An
        unlocked read-modify-write keeps only the last writer's entry, and a
        lost entry is a PR reviewed a second time."""
        cache = os.path.join(tmp_dir, "cache.json")
        r = self._py(
            "from concurrent.futures import ThreadPoolExecutor\n"
            "from lib.file_lock import locked_json_update\n"
            "def add(n):\n"
            f"    with locked_json_update({cache!r}) as data:\n"
            "        data[str(n)] = n\n"
            "with ThreadPoolExecutor(max_workers=16) as pool:\n"
            "    list(pool.map(add, range(40)))\n"
            "import json\n"
            f"print(len(json.load(open({cache!r}))))\n"
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "40", r.stdout

    def test_unreadable_json_is_replaced_not_appended_to(self, tmp_dir):
        """Callers treat a torn file as absent. The write still has to leave a
        valid document behind, or the file stays broken for good."""
        cache = os.path.join(tmp_dir, "torn.json")
        with open(cache, "w") as f:
            f.write('{"half": ')
        r = self._py(
            "import json\n"
            "from lib.file_lock import locked_json_update\n"
            f"with locked_json_update({cache!r}) as data:\n"
            "    data['ok'] = 1\n"
            f"print(json.load(open({cache!r})))\n"
        )
        assert r.returncode == 0, r.stderr
        assert "{'ok': 1}" in r.stdout, r.stdout

    def test_a_failed_update_leaves_the_previous_contents(self, tmp_dir):
        """Atomic because the alternative is a truncated file: the callers read
        that as no cache at all and re-review everything."""
        cache = os.path.join(tmp_dir, "keep.json")
        with open(cache, "w") as f:
            f.write('{"before": 1}')
        r = self._py(
            "from lib.file_lock import locked_json_update\n"
            "try:\n"
            f"    with locked_json_update({cache!r}) as data:\n"
            "        data['during'] = 2\n"
            "        raise RuntimeError('killed')\n"
            "except RuntimeError:\n"
            "    pass\n"
        )
        assert r.returncode == 0, r.stderr
        with open(cache) as f:
            assert f.read() == '{"before": 1}'


class TestTargetRepoSync:
    """`sync-target-repo.sh` resets the target repo's default branch to
    upstream. It sits in an `&&` chain ahead of run.sh and every review job, so
    a non-zero exit here cancels the work behind it — which is exactly what a
    lost race with a concurrent fetch produces."""

    @staticmethod
    def _bot_with_target(tmp_dir):
        """A bot directory whose target repo has an `upstream` ahead of it."""
        upstream = os.path.join(tmp_dir, "upstream.git")
        subprocess.run(
            ["git", "init", "--bare", "-b", "master", upstream],
            check=True,
            capture_output=True,
        )
        seed = os.path.join(tmp_dir, "seed")
        subprocess.run(
            ["git", "clone", upstream, seed], check=True, capture_output=True
        )
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        for text in ("one", "two"):
            with open(os.path.join(seed, "f.txt"), "w") as f:
                f.write(text)
            subprocess.run(
                ["git", "-C", seed, "add", "f.txt"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", seed, "commit", "-m", text],
                check=True,
                capture_output=True,
                env=env,
            )
        subprocess.run(
            ["git", "-C", seed, "push", "origin", "master"],
            check=True,
            capture_output=True,
        )

        target = os.path.join(tmp_dir, "target")
        subprocess.run(
            ["git", "clone", upstream, target], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", target, "remote", "rename", "origin", "upstream"],
            check=True,
            capture_output=True,
        )
        # One commit behind, and a stray local commit a reset has to discard.
        subprocess.run(
            ["git", "-C", target, "reset", "--hard", "HEAD~1"],
            check=True,
            capture_output=True,
        )

        bot = os.path.join(tmp_dir, "bot")
        os.makedirs(os.path.join(bot, ".ignore"))
        shutil.copytree(
            os.path.join(REPO_ROOT, "scripts"),
            os.path.join(bot, "scripts"),
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        with open(os.path.join(bot, "config.json"), "w") as f:
            json.dump(
                {
                    "project": {
                        "name": "p",
                        "org": "o",
                        "prRepository": "o/p",
                        "issueRepository": "o/p",
                        "defaultBranch": "master",
                        "targetRepoPath": target,
                    },
                    "bot": {"username": "b"},
                },
                f,
            )
        return bot, target

    @staticmethod
    def _head_subject(repo):
        return subprocess.run(
            ["git", "-C", repo, "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_the_branch_is_reset_to_upstream(self, tmp_dir):
        bot, target = self._bot_with_target(tmp_dir)
        assert self._head_subject(target) == "one"
        r = subprocess.run(
            [os.path.join(bot, "scripts", "sync-target-repo.sh")],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert self._head_subject(target) == "two", r.stdout + r.stderr

    def test_the_sync_waits_for_the_repo_lock(self, tmp_dir):
        """The reason it is one critical section and not three bare commands:
        a fetch another process is midway through is what breaks the reset."""
        bot, target = self._bot_with_target(tmp_dir)
        lock_lib = os.path.join(bot, "scripts", "lib", "lock.sh")
        lockfile = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, {os.path.join(bot, 'scripts')!r});"
                "from lib.repo_lock import lock_path;"
                f"print(lock_path({target!r}, bot_dir={bot!r}))",
            ],
            capture_output=True,
            text=True,
        )
        assert lockfile.returncode == 0, lockfile.stderr
        lockfile = lockfile.stdout.strip()

        holder = subprocess.Popen(
            [
                "bash",
                "-c",
                f"source {lock_lib}\n"
                f'bot_acquire_lock "{lockfile}" || exit 1\necho held\nexec sleep 30',
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            sync = subprocess.Popen(
                [os.path.join(bot, "scripts", "sync-target-repo.sh")],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            with pytest.raises(subprocess.TimeoutExpired):
                sync.wait(timeout=3)
            assert self._head_subject(target) == "one", (
                "it reset while the lock was held"
            )
        finally:
            holder.kill()
            holder.wait()
        assert sync.wait(timeout=30) == 0, sync.communicate()
        assert self._head_subject(target) == "two"
