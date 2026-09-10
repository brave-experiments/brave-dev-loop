"""Tests for all Python scripts in brave-dev-loop.

Covers: update-prd-status.py, select-task.py, business-hours-elapsed.py,
and check-prd-has-work.py.
"""

import json
import os
import re
import subprocess
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone

import pytest

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")
PROJECTS_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "projects")
DOCS_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "docs")
UPDATE_PRD_SCRIPT = os.path.join(SCRIPT_DIR, "update-prd-status.py")


# ── Helpers ──────────────────────────────────────────────────────────────────


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
        for status in ("pending", "committed", "pushed", "merged"):
            assert (
                update_prd_status.validate_transition("skipped", make_story(status))
                is None
            )

    def test_skipped_rejected_from_terminal(self, update_prd_status):
        for status in ("skipped", "invalid"):
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

    def test_merged_check_rejects_final_state(self, update_prd_status):
        story = make_story("merged", mergedCheckFinalState=True)
        assert update_prd_status.validate_transition("merged-check", story) is not None


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
        assert story["nextMergedCheck"] is not None
        assert story["mergedCheckCount"] == 0
        assert story["mergedCheckFinalState"] is False

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

    def test_merged_check_full_backoff(self, update_prd_status):
        """Verify backoff: count 0->1->2->3->4(final)."""
        story = make_story("merged", mergedCheckCount=0, mergedCheckFinalState=False)
        args = Namespace()

        for expected_count in (1, 2, 3):
            update_prd_status.handle_merged_check(story, args)
            assert story["mergedCheckCount"] == expected_count
            assert story["mergedCheckFinalState"] is False
            assert story["nextMergedCheck"] is not None

        update_prd_status.handle_merged_check(story, args)
        assert story["mergedCheckCount"] == 4
        assert story["mergedCheckFinalState"] is True
        assert story["nextMergedCheck"] is None


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
        for cmd in ("set-ping", "set-branch", "merged-check"):
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

    def test_merged_is_low(self, select_task):
        assert select_task.assign_tier(make_story("merged")) == select_task.TIER_LOW


class TestSelectTaskFilter:
    def test_excludes_skipped_and_invalid(self, select_task):
        stories = [
            make_story("skipped", id="US-001"),
            make_story("invalid", id="US-002"),
            make_story("pending", id="US-003"),
        ]
        result = select_task.filter_stories(stories, empty_run_state())
        assert [s["id"] for s in result] == ["US-003"]

    def test_excludes_merged_final_state(self, select_task):
        stories = [
            make_story("merged", id="US-001", mergedCheckFinalState=True),
            make_story("pending", id="US-002"),
        ]
        result = select_task.filter_stories(stories, empty_run_state())
        assert [s["id"] for s in result] == ["US-002"]

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

    def test_merged_excluded_when_backoff_disabled(self, select_task):
        stories = [
            make_story("merged", id="US-001", mergedCheckFinalState=False),
            make_story("pending", id="US-002"),
        ]
        result = select_task.filter_stories(
            stories, empty_run_state(enableMergeBackoff=False)
        )
        assert [s["id"] for s in result] == ["US-002"]

    def test_merged_backoff_not_due(self, select_task):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        stories = [
            make_story(
                "merged",
                id="US-001",
                mergedCheckFinalState=False,
                nextMergedCheck=future,
            )
        ]
        assert select_task.filter_stories(stories, empty_run_state()) == []

    def test_merged_backoff_due(self, select_task):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        stories = [
            make_story(
                "merged", id="US-001", mergedCheckFinalState=False, nextMergedCheck=past
            )
        ]
        assert [
            s["id"] for s in select_task.filter_stories(stories, empty_run_state())
        ] == ["US-001"]


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
        prd = {"stories": [make_story(description="Resolve issue #52439: x")]}
        assert 52439 in add_backlog.tracked_issue_numbers(prd)

    def test_archived_prd_counts_as_tracked(self, add_backlog):
        archived = {"stories": [make_story(description="Resolve issue #111: x")]}
        assert 111 in add_backlog.tracked_issue_numbers({"stories": []}, archived)

    def test_missing_archived_prd_is_tolerated(self, add_backlog):
        assert add_backlog.tracked_issue_numbers({"stories": []}, None) == set()

    def test_unrelated_hash_is_not_tracked(self, add_backlog):
        prd = {"stories": [make_story(description="Land PR #52439 in repo")]}
        assert add_backlog.tracked_issue_numbers(prd) == set()


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
                    make_story(status="merged", description="Resolve issue #903: x")
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

    def test_docs_dir_matches_target_repo_in_reference_configs(self):
        """docsDir is bot-dir-relative; targetRepoPath may be either base."""
        for name in ("config.brave-core.json", "config.example.json"):
            cfg = self._load(name)
            target = cfg["project"]["targetRepoPath"].lstrip("./")
            docs = cfg["bestPractices"]["docsDir"]
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

    def test_the_pointers_stay_project_neutral(self):
        """Shared docs serve every profile; brave-core has no worktrees."""
        for name in sorted(os.listdir(DOCS_DIR)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(DOCS_DIR, name)) as f:
                text = f.read()
            assert "bravebot" not in text, f"docs/{name} hard-codes bravebot"


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
        """This job used to spend a whole agent session on a deterministic sync."""
        with open(os.path.join(SCRIPT_DIR, "sync-schedules.sh")) as f:
            body = f.read()
        assert "add-backlog -- ./scripts/sync-prd.sh" in body
        assert "/add-backlog-to-prd'" not in body


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

    def test_no_call_site_still_uses_bare_flock(self):
        # run.sh takes a numbered run slot; with-lock.sh takes a named lock.
        # Either way the lock library owns the flock, not the call site.
        entry_points = {
            "../run.sh": "bot_acquire_run_slot",
            "with-lock.sh": "bot_acquire_lock",
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
        root = os.path.join(SCRIPT_DIR, os.pardir)
        for name in ("config.example.json", "config.brave-core.json"):
            with open(os.path.join(root, name)) as f:
                cfg = json.load(f)["project"]
            named = os.path.join(root, "projects", cfg["name"], "profile.json")
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
