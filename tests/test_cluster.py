"""Clustering related issues into one iteration (lib/cluster.py and its callers)."""

import argparse
import importlib.util
import json
import os
import sys

import pytest

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")
sys.path.insert(0, os.path.abspath(SCRIPT_DIR))

from lib import cluster  # noqa: E402

BRAVEBOT_PROFILE = os.path.join(
    os.path.dirname(__file__), os.pardir, "projects", "bravebot", "profile.json"
)


def story(sid, size=1, status="pending", issue=None, **extra):
    """A story as add-backlog-to-prd builds it: the issue lives in the description."""
    s = {"id": sid, "title": f"Story {sid}", "status": status}
    if issue is not False:
        number = issue if issue is not None else int(sid.split("-")[1])
        s["description"] = f"Resolve issue #{number}: story {sid}"
    if size is not None:
        s["triage"] = {"size": size}
    s.update(extra)
    return s


def profile(limits=None):
    return {"clustering": {"sizeLimits": limits}} if limits else {}


TABLE = {"1": 10, "2": 6, "3": 4, "4": 1, "5": 1}


class TestCeiling:
    @pytest.mark.parametrize("size,expected", [(1, 10), (2, 6), (3, 4), (4, 1), (5, 1)])
    def test_the_requested_table(self, size, expected):
        assert cluster.ceiling(profile(TABLE), story("US-001", size)) == expected

    def test_the_bravebot_profile_carries_that_table(self):
        with open(BRAVEBOT_PROFILE) as f:
            real = json.load(f)
        for size, expected in ((1, 10), (2, 6), (3, 4), (4, 1), (5, 1)):
            assert cluster.ceiling(real, story("US-001", size)) == expected

    def test_a_profile_without_the_block_never_clusters(self):
        assert cluster.ceiling({}, story("US-001", 1)) == 1

    def test_unlabelled_lead_is_worked_alone(self):
        """Unlike ordering, an unknown size is not the middle one: guessing
        wrong here puts ten issues in a pull request."""
        assert cluster.ceiling(profile(TABLE), story("US-001", None)) == 1

    def test_malformed_entries_are_dropped_not_raised(self):
        p = profile({"1": "many", "x": 3, "2": 0, "3": 4, "9": 5})
        assert cluster.size_limits(p) == {3: 4}


class TestPool:
    def test_only_pending_stories_with_an_issue(self):
        lead = story("US-001", 2)
        ordered = [
            story("US-002", 1),
            story("US-003", 1, status="pushed"),
            story("US-004", 1, issue=False),
        ]
        pool = cluster.companion_pool(lead, ordered, 6)
        assert [s["id"] for s in pool] == ["US-002"]

    def test_never_larger_than_the_lead(self):
        lead = story("US-001", 2)
        ordered = [story("US-002", 3), story("US-003", 2), story("US-004", 1)]
        pool = cluster.companion_pool(lead, ordered, 6)
        assert [s["id"] for s in pool] == ["US-003", "US-004"]

    def test_unlabelled_candidates_are_not_offered(self):
        lead = story("US-001", 1)
        assert cluster.companion_pool(lead, [story("US-002", None)], 10) == []

    def test_lead_is_not_its_own_companion(self):
        lead = story("US-001", 1)
        assert cluster.companion_pool(lead, [lead], 10) == []

    def test_issues_in_progress_elsewhere_are_not_offered(self):
        lead = story("US-001", 1)
        pool = cluster.companion_pool(
            lead, [story("US-002", 1)], 10, blocked_issues={2}
        )
        assert pool == []

    def test_ceiling_of_one_offers_nothing(self):
        lead = story("US-001", 4)
        assert cluster.companion_pool(lead, [story("US-002", 1)], 1) == []

    def test_pool_is_capped(self):
        lead = story("US-001", 1)
        ordered = [story(f"US-{n:03d}", 1) for n in range(2, 200)]
        assert len(cluster.companion_pool(lead, ordered, 10)) == cluster.POOL_CAP

    def test_order_is_kept(self):
        lead = story("US-001", 1)
        ordered = [story("US-009", 1), story("US-003", 1)]
        pool = cluster.companion_pool(lead, ordered, 10)
        assert [s["id"] for s in pool] == ["US-009", "US-003"]


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), os.path.join(SCRIPT_DIR, name + ".py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSelectOffer:
    @pytest.fixture
    def select_task(self, monkeypatch):
        mod = load_script("select-task")
        monkeypatch.setattr(mod, "load_config", lambda *a, **k: {})
        monkeypatch.setattr(mod, "load_profile", lambda *a, **k: profile(TABLE))
        return mod

    def test_offers_a_pool_sized_by_the_lead(self, select_task):
        lead = story("US-001", 2)
        others = [story("US-002", 1), story("US-003", 2), story("US-004", 5)]
        offer = select_task.clustering_offer("/bot", lead, [lead] + others, set(), None)
        assert offer["limit"] == 6
        assert offer["leadSize"] == 2
        assert sorted(c["storyId"] for c in offer["candidates"]) == ["US-002", "US-003"]

    def test_nothing_for_a_large_lead(self, select_task):
        lead = story("US-001", 4)
        assert (
            select_task.clustering_offer(
                "/bot", lead, [lead, story("US-002", 1)], set(), None
            )
            is None
        )

    def test_nothing_for_a_story_that_already_has_a_pr(self, select_task):
        lead = story("US-001", 1, status="pushed", prNumber=5)
        assert (
            select_task.clustering_offer(
                "/bot", lead, [lead, story("US-002", 1)], set(), None
            )
            is None
        )

    def test_nothing_when_no_candidate_qualifies(self, select_task):
        lead = story("US-001", 1)
        assert select_task.clustering_offer("/bot", lead, [lead], set(), None) is None


class TestStatusFollowsLead:
    @pytest.fixture
    def ups(self, monkeypatch):
        mod = load_script("update-prd-status")
        monkeypatch.setattr(
            mod, "load_config", lambda *a, **k: {"project": {"prRepository": "o/r"}}
        )
        monkeypatch.setattr(mod, "require_config", lambda c, k: "o/r")
        return mod

    def prd(self):
        lead = story("US-001", 2, clusterCompanions=["US-002", "US-003"])
        return {
            "stories": [
                lead,
                story("US-002", 1, clusterLead="US-001"),
                story("US-003", 1, clusterLead="US-001"),
                story("US-004", 1),
            ]
        }

    def test_committed_and_pushed_move_the_companions(self, ups):
        prd = self.prd()
        lead = prd["stories"][0]
        args = argparse.Namespace(branch="fix-a", pr_number=77)
        ups.handle_committed(lead, args)
        moved = ups.apply_to_companions(prd, lead, "committed", args)
        assert sorted(moved) == ["US-002", "US-003"]
        ups.handle_pushed(lead, args)
        ups.apply_to_companions(prd, lead, "pushed", args)
        for sid in ("US-002", "US-003"):
            s = prd["stories"][int(sid[-1]) - 1]
            assert (s["status"], s["branchName"], s["prNumber"]) == (
                "pushed",
                "fix-a",
                77,
            )
        assert prd["stories"][3]["status"] == "pending"

    def test_a_companion_in_another_status_is_left_alone(self, ups):
        prd = self.prd()
        prd["stories"][1]["status"] = "skipped"
        lead = prd["stories"][0]
        args = argparse.Namespace(branch="fix-a", pr_number=77)
        ups.handle_committed(lead, args)
        moved = ups.apply_to_companions(prd, lead, "committed", args)
        assert list(moved) == ["US-003"]
        assert prd["stories"][1]["status"] == "skipped"

    def test_skipping_the_lead_returns_companions_to_the_queue(self, ups):
        prd = self.prd()
        lead = prd["stories"][0]
        args = argparse.Namespace(reason="blocked")
        ups.handle_skipped(lead, args)
        ups.apply_to_companions(prd, lead, "skipped", args)
        assert "clusterLead" not in prd["stories"][1]
        assert prd["stories"][1]["status"] == "pending"
        assert "clusterCompanions" not in lead

    def test_a_story_without_companions_is_unaffected(self, ups):
        prd = {"stories": [story("US-001", 1)]}
        args = argparse.Namespace(branch="b")
        assert ups.apply_to_companions(prd, prd["stories"][0], "committed", args) == {}


class TestClaimCompanions:
    @pytest.fixture
    def cc(self, monkeypatch, tmp_path):
        mod = load_script("claim-companions")
        self.claimed = {}
        held = {"US-001": {"slot": 1, "pid": 99, "runId": "r"}}
        monkeypatch.setattr(mod.claims_lib, "active", lambda *a, **k: held)

        def claim(bot_dir, sid, slot, pid, run_id=None, locked=False):
            held[sid] = {"slot": slot, "pid": pid, "runId": run_id}
            self.claimed[sid] = slot
            return True

        monkeypatch.setattr(mod.claims_lib, "claim", claim)
        monkeypatch.setattr(mod.claims_lib, "release", lambda *a, **k: [])
        monkeypatch.setattr(mod.issue_lock, "acquire", lambda *a, **k: True)
        self.held = held
        self.tmp = tmp_path
        return mod

    def write_prd(self, stories):
        path = self.tmp / "prd.json"
        path.write_text(json.dumps({"stories": stories}))
        return str(path)

    def run(self, cc, stories, wanted, lead_size=2, **kw):
        stories = [story("US-001", lead_size)] + stories
        path = self.write_prd(stories)
        result, code = cc.claim_companions(
            str(self.tmp),
            path,
            "US-001",
            wanted,
            1,
            profile(TABLE),
            kw.get("blocked", set()),
        )
        return result, code, json.loads(open(path).read())

    def test_claims_up_to_the_ceiling_and_refuses_the_rest(self, cc):
        others = [story(f"US-{n:03d}", 1) for n in range(2, 10)]
        wanted = [s["id"] for s in others]
        result, code, prd = self.run(cc, others, wanted, lead_size=3)  # ceiling 4
        assert code == 0
        assert result["claimed"] == ["US-002", "US-003", "US-004"]
        assert result["taken"] == 4
        assert set(result["refused"]) == {
            "US-005",
            "US-006",
            "US-007",
            "US-008",
            "US-009",
        }
        assert prd["stories"][0]["clusterCompanions"] == result["claimed"]
        assert prd["stories"][1]["clusterLead"] == "US-001"

    def test_a_second_call_counts_against_the_same_ceiling(self, cc):
        others = [story(f"US-{n:03d}", 1) for n in range(2, 8)]
        first, _, _ = self.run(cc, others, ["US-002", "US-003", "US-004"], lead_size=3)
        assert len(first["claimed"]) == 3
        # Lead now carries three; a fourth would be the fifth story.
        path = str(self.tmp / "prd.json")
        second, _ = cc.claim_companions(
            str(self.tmp), path, "US-001", ["US-005"], 1, profile(TABLE), set()
        )
        assert second["claimed"] == []
        assert "ceiling" in second["refused"]["US-005"]

    def test_a_large_lead_cannot_take_any(self, cc):
        result, code, _ = self.run(cc, [story("US-002", 1)], ["US-002"], lead_size=4)
        assert code == 1
        assert "does not cluster" in result["error"]

    def test_a_lead_this_slot_does_not_hold_fails(self, cc):
        self.held.clear()
        result, code, _ = self.run(cc, [story("US-002", 1)], ["US-002"])
        assert code == 1

    def test_larger_candidate_is_refused(self, cc):
        result, code, _ = self.run(cc, [story("US-002", 3)], ["US-002"], lead_size=2)
        assert code == 0
        assert result["claimed"] == []
        assert "US-002" in result["refused"]

    def test_unknown_and_duplicate_ids_are_refused(self, cc):
        result, _, _ = self.run(
            cc, [story("US-002", 1)], ["US-002", "US-002", "US-404", "US-001"]
        )
        assert result["claimed"] == ["US-002"]
        assert set(result["refused"]) == {"US-404", "US-001"} | set()

    def test_a_story_another_run_holds_is_refused(self, cc):
        self.held["US-002"] = {"slot": 2, "pid": 5, "runId": "x"}
        result, _, _ = self.run(cc, [story("US-002", 1)], ["US-002"])
        assert result["claimed"] == []
        assert "another run" in result["refused"]["US-002"]

    def test_issue_in_progress_elsewhere_is_refused(self, cc):
        result, _, _ = self.run(cc, [story("US-002", 1)], ["US-002"], blocked={2})
        assert result["claimed"] == []

    def test_label_failure_hands_the_claim_back(self, cc, monkeypatch):
        released = []
        monkeypatch.setattr(cc.issue_lock, "acquire", lambda *a, **k: False)
        monkeypatch.setattr(
            cc.claims_lib, "release", lambda *a, **k: released.append(k.get("story_id"))
        )
        result, _, _ = self.run(cc, [story("US-002", 1)], ["US-002"])
        assert result["claimed"] == []
        assert released == ["US-002"]
