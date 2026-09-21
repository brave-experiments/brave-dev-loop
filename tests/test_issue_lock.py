"""Tests for the cross-machine story claim held as an issue label.

scripts/lib/issue_lock.py is the half of a claim that a run on another machine
can read. Every test here stands in for gh, so nothing reaches GitHub.
"""

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")
sys.path.insert(0, SCRIPTS)
from lib import issue_lock  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

# A config with the label on. The default profile defines none, so the label
# comes from the config override -- which is also how a deployment turns this
# on for a project whose profile is silent about it.
ON = {
    "project": {"profile": "default", "issueRepository": "test-org/test-project"},
    "bot": {"username": "test-bot"},
    "labels": {"inProgressLabel": "bot/in-progress"},
}
OFF = {
    "project": {"profile": "default", "issueRepository": "test-org/test-project"},
    "bot": {"username": "test-bot"},
}


class Gh:
    """Stands in for the gh CLI, answering by subcommand rather than by order.

    ``issues`` is what `issue list` returns (None to fail the read), ``events``
    maps an issue number to the (label, timestamp) pairs its event log holds.
    """

    def __init__(self, issues="[]", events=None):
        self.calls = []
        self.issues = issues
        self.events = events or {}

    def __call__(self, args, timeout=120):
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            return self.issues
        if args[0] == "api":
            number = int(args[2].rsplit("/", 2)[1])
            lines = [
                json.dumps({"name": name, "at": at})
                for name, at in self.events.get(number, [])
            ]
            return "\n".join(lines) + "\n" if lines else ""
        return ""

    def edits(self):
        return [a for a in self.calls if a[:2] == ["issue", "edit"]]


@pytest.fixture
def gh(monkeypatch):
    def _install(**kwargs):
        fake = Gh(**kwargs)
        monkeypatch.setattr(issue_lock, "_gh", fake)
        return fake

    return _install


def _hours_ago(hours):
    return (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestIssueNumber:
    def test_parses_the_description(self):
        assert issue_lock.issue_number({"description": "Resolve issue #133"}) == 133

    def test_none_without_a_reference(self):
        assert issue_lock.issue_number({"description": "nothing here"}) is None

    def test_none_without_a_description(self):
        assert issue_lock.issue_number({}) is None


class TestLabelName:
    def test_the_bravebot_profile_defines_one(self):
        """The three machines only stop colliding if the shipped profile has it."""
        label = issue_lock.label_name({"project": {"profile": "bravebot"}})
        assert label == "bot/in-progress"

    def test_a_profile_without_one_falls_back_to_the_config(self):
        assert issue_lock.label_name(ON) == "bot/in-progress"

    def test_the_profile_wins_over_the_config(self):
        config = dict(ON, project={"profile": "bravebot"})
        assert issue_lock.label_name(config) == "bot/in-progress"

    def test_empty_where_neither_defines_one(self):
        """A project run from one machine needs no cross-machine claim."""
        assert issue_lock.label_name(OFF) == ""


class TestInProgress:
    def test_returns_the_labelled_issue_numbers(self, gh):
        gh(issues=json.dumps([{"number": 101}, {"number": 205}]))
        assert issue_lock.in_progress(ON) == {101, 205}

    def test_asks_github_nothing_when_no_label_is_configured(self, gh):
        fake = gh(issues=json.dumps([{"number": 101}]))
        assert issue_lock.in_progress(OFF) == set()
        assert fake.calls == []

    def test_a_failed_read_blocks_nothing(self, gh):
        """Fail open: a GitHub hiccup must not read as "every issue is taken"."""
        gh(issues=None)
        assert issue_lock.in_progress(ON) == set()

    def test_unparseable_output_blocks_nothing(self, gh):
        gh(issues="not json")
        assert issue_lock.in_progress(ON) == set()

    def test_is_not_narrowed_to_the_bots_own_issues(self, gh):
        """Whoever an issue is assigned to, the label means somebody is on it."""
        fake = gh(issues="[]")
        issue_lock.in_progress(ON)
        assert "--assignee" not in fake.calls[0]


class TestAcquire:
    def test_adds_the_label(self, gh):
        fake = gh()
        assert issue_lock.acquire(101, ON) is True
        assert fake.edits() == [
            [
                "issue",
                "edit",
                "101",
                "--repo",
                "test-org/test-project",
                "--add-label",
                "bot/in-progress",
            ]
        ]

    def test_no_label_configured_is_not_an_acquire(self, gh):
        fake = gh()
        assert issue_lock.acquire(101, OFF) is False
        assert fake.calls == []

    def test_a_story_with_no_issue_is_not_an_acquire(self, gh):
        fake = gh()
        assert issue_lock.acquire(None, ON) is False
        assert fake.calls == []

    def test_a_failed_add_is_reported_not_raised(self, gh, monkeypatch):
        monkeypatch.setattr(issue_lock, "_gh", lambda args, timeout=120: None)
        assert issue_lock.acquire(101, ON) is False


class TestRelease:
    def test_removes_the_label(self, gh):
        fake = gh()
        assert issue_lock.release(101, ON) is True
        assert fake.edits()[0][-2:] == ["--remove-label", "bot/in-progress"]

    def test_no_label_configured_is_not_a_release(self, gh):
        fake = gh()
        assert issue_lock.release(101, OFF) is False
        assert fake.calls == []


class TestLabelledAt:
    def test_the_newest_application_wins(self, gh):
        """A label taken off and put back on is a fresh claim, not an old one."""
        gh(
            events={
                101: [
                    ("bot/in-progress", _hours_ago(30)),
                    ("bot/in-progress", _hours_ago(1)),
                ]
            }
        )
        applied = issue_lock.labelled_at(101, "bot/in-progress", ON)
        assert applied == datetime.fromisoformat(_hours_ago(1).replace("Z", "+00:00"))

    def test_other_labels_are_ignored(self, gh):
        gh(events={101: [("bug", _hours_ago(1))]})
        assert issue_lock.labelled_at(101, "bot/in-progress", ON) is None

    def test_none_when_the_issue_has_no_events(self, gh):
        gh(events={})
        assert issue_lock.labelled_at(101, "bot/in-progress", ON) is None

    def test_none_when_the_read_fails(self, monkeypatch):
        monkeypatch.setattr(issue_lock, "_gh", lambda args, timeout=120: None)
        assert issue_lock.labelled_at(101, "bot/in-progress", ON) is None


class TestStale:
    def _fake(self, gh, ages):
        return gh(
            issues=json.dumps([{"number": n} for n in ages]),
            events={
                n: [("bot/in-progress", _hours_ago(h))]
                for n, h in ages.items()
                if h is not None
            },
        )

    def test_a_label_inside_the_limit_is_left_alone(self, gh):
        self._fake(gh, {101: 2})
        assert issue_lock.stale(6, ON, now=NOW) == []

    def test_a_label_past_the_limit_is_stale(self, gh):
        self._fake(gh, {101: 7})
        assert issue_lock.stale(6, ON, now=NOW) == [101]

    def test_an_unreadable_age_counts_as_stale(self, gh):
        """Better a second machine picks it up than the issue parks for good."""
        self._fake(gh, {101: None})
        assert issue_lock.stale(6, ON, now=NOW) == [101]

    def test_only_the_bots_own_issues_are_considered(self, gh):
        """The same label on a human's issue is not the loop's to undo."""
        fake = self._fake(gh, {})
        issue_lock.stale(6, ON, now=NOW)
        listing = fake.calls[0]
        assert listing[listing.index("--assignee") + 1] == "test-bot"

    def test_nothing_is_stale_without_a_label(self, gh):
        fake = gh()
        assert issue_lock.stale(6, OFF, now=NOW) == []
        assert fake.calls == []

    def test_a_failed_listing_sweeps_nothing(self, gh):
        gh(issues=None)
        assert issue_lock.stale(6, ON, now=NOW) == []


class TestSweep:
    def test_releases_each_stale_label(self, gh):
        fake = gh(
            issues=json.dumps([{"number": 101}, {"number": 102}]),
            events={
                101: [("bot/in-progress", _hours_ago(9))],
                102: [("bot/in-progress", _hours_ago(1))],
            },
        )
        assert issue_lock.sweep(6, ON, now=NOW) == [101]
        assert [e[2] for e in fake.edits()] == ["101"]

    def test_a_dry_run_changes_nothing(self, gh):
        fake = gh(
            issues=json.dumps([{"number": 101}]),
            events={101: [("bot/in-progress", _hours_ago(9))]},
        )
        assert issue_lock.sweep(6, ON, now=NOW, dry_run=True) == [101]
        assert fake.edits() == []


class TestClaimsReleasesTheLabel:
    """A story handed back locally must stop looking worked to everybody else,
    so scripts/claims.py releases both halves of the claim together."""

    @staticmethod
    def _claims_cli():
        spec = importlib.util.spec_from_file_location(
            "claims_cli", os.path.join(SCRIPTS, "claims.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @pytest.fixture
    def released(self, tmp_path, monkeypatch):
        """A bot dir holding two stories, and the issues release() was called on."""
        os.makedirs(tmp_path / "data")
        stories = [
            {"id": "US-001", "description": "Resolve issue #101"},
            {"id": "US-002", "description": "Resolve issue #102"},
            {"id": "US-003", "description": "no issue at all"},
        ]
        with open(tmp_path / "data" / "prd.json", "w") as f:
            json.dump({"stories": stories}, f)
        calls = []
        monkeypatch.setattr(issue_lock, "label_name", lambda **kw: "bot/in-progress")
        monkeypatch.setattr(
            issue_lock, "release", lambda issue, **kw: calls.append(issue) or True
        )
        return calls

    def test_releases_the_issue_of_each_dropped_story(self, tmp_path, released):
        self._claims_cli()._release_labels(str(tmp_path), {"US-002"})
        assert released == [102]

    def test_leaves_the_stories_still_claimed_alone(self, tmp_path, released):
        self._claims_cli()._release_labels(str(tmp_path), {"US-001", "US-003"})
        assert released == [101]

    def test_nothing_dropped_is_nothing_released(self, tmp_path, released):
        self._claims_cli()._release_labels(str(tmp_path), set())
        assert released == []

    def test_no_label_configured_reads_no_prd(self, tmp_path, monkeypatch):
        monkeypatch.setattr(issue_lock, "label_name", lambda **kw: "")
        monkeypatch.setattr(
            issue_lock, "release", lambda issue, **kw: pytest.fail("released anyway")
        )
        self._claims_cli()._release_labels(str(tmp_path), {"US-001"})
