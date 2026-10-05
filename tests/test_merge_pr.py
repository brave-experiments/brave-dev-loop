"""`make approve` and `make merge`: approving, and squash-merging, as the bot's owner.

gh is stubbed on PATH and logs every call with the GH_TOKEN it was made under, so
the assertions are about which account each write ran as and what was asked of it.
"""

import json
import os
import subprocess

ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPT = os.path.join(ROOT_DIR, "scripts", "merge-pr.py")
CONFIG = os.path.join(os.path.dirname(__file__), "config.test.json")

STUB = """#!/bin/sh
echo "$GH_TOKEN|$GH_CONFIG_DIR|$*" >> "$GH_STUB_LOG"
case "$1 $2" in
  "auth token") [ -n "$GH_STUB_NO_LOGIN" ] && exit 1; printf owner-token ;;
  "api user") printf "%s" "${GH_STUB_LOGIN:-test-owner}" ;;
  "pr view") printf "%s" "$GH_STUB_JSON" ;;
  "pr merge") [ -n "$GH_STUB_MERGE_FAILS" ] && { echo "checks pending" >&2; exit 1; } ;;
esac
exit 0
"""


def run(
    tmp_path, action, *, state="OPEN", draft=False, author="someone", expect=0, **extra
):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "gh"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log = tmp_path / "gh.log"
    pr = {
        "state": state,
        "isDraft": draft,
        "title": "answer NO_COLOR",
        "headRefOid": "abc123",
        "author": {"login": author},
    }
    env = dict(
        os.environ,
        PATH=f"{bindir}:{os.environ['PATH']}",
        BOT_CONFIG_FILE=CONFIG,
        GH_STUB_LOG=str(log),
        GH_STUB_JSON=json.dumps(pr),
        GH_TOKEN="bot-token",
        GH_CONFIG_DIR="/bot/gh",
        **extra,
    )
    result = subprocess.run(
        ["python3", SCRIPT, action, "351", "--no-input"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == expect, result.stderr
    calls = log.read_text().splitlines() if log.exists() else []
    return result, [c.split("|", 2) for c in calls]


def writes(calls):
    return [c for c in calls if c[2].startswith(("pr review", "pr merge"))]


def test_approve_only_approves_and_does_not_merge(tmp_path):
    _, calls = run(tmp_path, "approve")

    assert [c[2] for c in writes(calls)] == [
        "pr review 351 --repo test-org/test-project --approve"
    ]


def test_merge_approves_then_squash_merges_the_approved_commit(tmp_path):
    _, calls = run(tmp_path, "merge")

    assert [c[2] for c in writes(calls)] == [
        "pr review 351 --repo test-org/test-project --approve",
        "pr merge 351 --repo test-org/test-project --squash --match-head-commit abc123",
    ]


def test_every_call_runs_as_the_owner_and_not_the_bot(tmp_path):
    """The shell's GH_TOKEN and GH_CONFIG_DIR are the bot's; neither may survive."""
    _, calls = run(tmp_path, "merge")

    reads = [c for c in calls if c[2].startswith(("api user", "pr "))]
    assert reads and all(c[0] == "owner-token" and c[1] == "" for c in reads)


def test_refuses_when_gh_resolves_to_another_account(tmp_path):
    result, calls = run(tmp_path, "merge", expect=1, GH_STUB_LOGIN="netzenbot")

    assert "not test-owner" in result.stderr
    assert writes(calls) == []


def test_refuses_when_the_owner_has_no_stored_login(tmp_path):
    result, calls = run(tmp_path, "approve", expect=1, GH_STUB_NO_LOGIN="1")

    assert "no stored login" in result.stderr
    assert writes(calls) == []


def test_a_closed_pull_request_is_left_alone(tmp_path):
    result, calls = run(tmp_path, "merge", state="MERGED", expect=1)

    assert "merged" in result.stderr
    assert writes(calls) == []


def test_a_draft_is_not_merged_or_approved_by_merge(tmp_path):
    _, calls = run(tmp_path, "merge", draft=True, expect=1)

    assert writes(calls) == []


def test_owner_authored_pull_request_merges_without_an_approval(tmp_path):
    _, calls = run(tmp_path, "merge", author="test-owner")

    assert [c[2].split()[1] for c in writes(calls)] == ["merge"]


def test_owner_authored_pull_request_cannot_be_approved(tmp_path):
    result, calls = run(tmp_path, "approve", author="test-owner", expect=1)

    assert "self-approval" in result.stderr
    assert writes(calls) == []


def test_a_merge_github_refuses_is_reported_and_the_approval_stays(tmp_path):
    result, calls = run(tmp_path, "merge", expect=1, GH_STUB_MERGE_FAILS="1")

    assert "checks pending" in result.stderr
    assert [c[2].split()[1] for c in writes(calls)] == ["review", "merge"]
