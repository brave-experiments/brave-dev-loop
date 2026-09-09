"""Tests for all Python scripts in brave-dev-bot.

Covers: update-prd-status.py, select-task.py, business-hours-elapsed.py,
and check-prd-has-work.py.
"""

import json
import os
import subprocess
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")
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
    def test_docs_only_story_omits_build_criteria(self, sync_bot_prs):
        story = sync_bot_prs.build_pr_story(333, 332, make_pr())
        criteria = " ".join(story["acceptanceCriteria"])
        assert "Build the project" not in criteria
        assert "presubmit" not in criteria
        assert story["docsOnly"] is True

    def test_code_story_includes_build_criteria(self, sync_bot_prs):
        story = sync_bot_prs.build_pr_story(
            333, 332, make_pr(files=("brave/browser/x.cc",))
        )
        criteria = " ".join(story["acceptanceCriteria"])
        assert "Build the project (must pass)" in criteria
        assert "presubmit" in criteria
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
        result = run_identity_snippet(
            "set -e\nbot_export_identity_env '' ''\necho \"ssh=[$GIT_SSH_COMMAND]\"",
            env={"PATH": f"{bindir}:{os.environ['PATH']}", "GH_TOKEN": ""},
        )
        assert result.returncode == 0
        assert "ssh=[]" in result.stdout


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

    def test_disabled_test_label_is_a_disabled_issue(self, add_backlog):
        issue = make_issue(
            title="Re-enable FooTest.Bar", labels=("disabled-brave-test",)
        )
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
