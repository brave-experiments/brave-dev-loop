"""The org gate: PRs from non-members never reach a review job, and no model
is involved in saying so."""

import sys

import pytest
from conftest import SCRIPTS_DIR

sys.path.insert(0, SCRIPTS_DIR)
from lib import org_gate  # noqa: E402


@pytest.fixture
def members(tmp_path, monkeypatch):
    path = tmp_path / "org-members.txt"
    path.write_text("Alice\nbob\n")
    monkeypatch.setattr(org_gate, "members_path", lambda: str(path))
    return path


def test_missing_members_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(org_gate, "members_path", lambda: str(tmp_path / "nope"))
    with pytest.raises(org_gate.OrgGateError):
        org_gate.split_prs([{"number": 1, "author": {"login": "x"}}])


def test_empty_members_file_fails_closed(members):
    members.write_text("\n")
    with pytest.raises(org_gate.OrgGateError):
        org_gate.load_members()


def test_split_separates_non_members(members):
    prs = [
        {"number": 1, "author": {"login": "alice"}},
        {"number": 2, "author": {"login": "mallory"}},
        {"number": 3, "author": "bob"},
        {"number": 4, "author": None},
    ]
    allowed, skipped = org_gate.split_prs(prs)
    assert [p["number"] for p in allowed] == [1, 3]
    assert [p["number"] for p in skipped] == [2, 4]


def test_nothing_is_ever_posted():
    assert not hasattr(org_gate, "comment_once")
    assert not hasattr(org_gate, "_gh")


def test_filter_queue_prints_only_members(members, monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO("1 alice\n2 mallory\n3 bob\n"))
    assert org_gate.main(["filter-queue"]) == 0
    assert capsys.readouterr().out.split() == ["1", "3"]


def test_filter_queue_fails_when_the_members_file_is_gone(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(org_gate, "members_path", lambda: str(tmp_path / "nope"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("1 alice\n"))
    assert org_gate.main(["filter-queue"]) == 1
