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
        org_gate.split_prs("o/r", [{"number": 1, "author": {"login": "x"}}], print)


def test_empty_members_file_fails_closed(members):
    members.write_text("\n")
    with pytest.raises(org_gate.OrgGateError):
        org_gate.load_members()


def test_split_comments_only_on_non_members(members, monkeypatch):
    commented = []
    monkeypatch.setattr(
        org_gate, "comment_once", lambda repo, n, log: commented.append(n)
    )
    prs = [
        {"number": 1, "author": {"login": "alice"}},
        {"number": 2, "author": {"login": "mallory"}},
        {"number": 3, "author": "bob"},
        {"number": 4, "author": None},
    ]
    allowed, skipped = org_gate.split_prs("o/r", prs, print)
    assert [p["number"] for p in allowed] == [1, 3]
    assert [p["number"] for p in skipped] == [2, 4]
    assert commented == [2, 4]


def test_comment_failure_still_drops_the_pr(members, monkeypatch):
    def boom(repo, n, log):
        raise org_gate.OrgGateError("api down")

    monkeypatch.setattr(org_gate, "comment_once", boom)
    allowed, skipped = org_gate.split_prs(
        "o/r", [{"number": 2, "author": "eve"}], print
    )
    assert allowed == [] and len(skipped) == 1


def test_comment_is_not_repeated(monkeypatch):
    calls = []
    monkeypatch.setattr(org_gate, "_current_login", lambda: "netzenbot")
    monkeypatch.setattr(org_gate, "already_commented", lambda *a: True)
    monkeypatch.setattr(org_gate, "_gh", lambda args, **k: calls.append(args))
    assert org_gate.comment_once("o/r", 5, print) is False
    assert calls == []


def test_comment_text_is_fixed():
    assert org_gate.SKIP_COMMENT.startswith(
        "Review skipped because not a registered org member."
    )
    assert org_gate.SKIP_MARKER in org_gate.SKIP_COMMENT


def test_filter_queue_prints_only_members(members, monkeypatch, capsys):
    import io

    monkeypatch.setattr(org_gate, "comment_once", lambda *a: True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("1 alice\n2 mallory\n3 bob\n"))
    assert org_gate.main(["filter-queue", "--repo", "o/r"]) == 0
    assert capsys.readouterr().out.split() == ["1", "3"]


def test_filter_queue_fails_when_the_members_file_is_gone(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(org_gate, "members_path", lambda: str(tmp_path / "nope"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("1 alice\n"))
    assert org_gate.main(["filter-queue", "--repo", "o/r"]) == 1


def test_marker_from_another_account_stops_the_comment(monkeypatch):
    posted = []

    def fake_gh(args, **k):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = R()
        if "POST" in args:
            posted.append(args)
        elif args[:2] == ["api", "user"]:
            r.stdout = "netzenbot\n"
        else:
            # the reviewer account already left the marker comment
            r.stdout = "123\n"
        return r

    monkeypatch.setattr(org_gate, "_gh", fake_gh)
    assert org_gate.comment_once("o/r", 5, print) is False
    assert posted == []
