"""The PR-feedback gate: when it wakes an agent, and when it must not."""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "pr_feedback", os.path.join(HERE, os.pardir, "scripts", "pr-feedback.py")
)
pf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pf)

NOW = 1_000_000_000.0
BOT = "netzenbot"
TRUSTED = {"alice", "netzenbot-reviewer"}
SETTINGS = dict(pf.DEFAULTS)


def review(login, state, id_, at="2026-01-01T00:00:00Z"):
    return {"author": {"login": login}, "state": state, "id": id_, "submittedAt": at}


def pr(reviews=(), checks=(), draft=False, head="h1"):
    return {
        "number": 7,
        "headRefOid": head,
        "isDraft": draft,
        "reviews": list(reviews),
        "statusCheckRollup": list(checks),
    }


def failing(name="build"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": "COMPLETED",
        "conclusion": "FAILURE",
    }


def passing(name="build"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
    }


def state(*attempts):
    return {"attempts": [dict(a) for a in attempts], "notBefore": 0}


def attempt(component, outcome, ago=3600 * 5, **extra):
    at = NOW - ago
    return {
        "at": at,
        "components": [component],
        "outcome": outcome,
        "finishedAt": at + 60,
        **extra,
    }


def ev(p, s=None, settings=None, now=NOW):
    return pf.evaluate(p, s or state(), now, settings or SETTINGS, BOT, TRUSTED)


def test_nothing_to_do_is_free():
    assert not ev(pr([review("alice", "APPROVED", "r1")], [passing()]))["eligible"]


def test_request_changes_wakes():
    v = ev(pr([review("alice", "CHANGES_REQUESTED", "r1")]))
    assert v["eligible"] and v["components"] == ["cr:alice:r1"]


def test_later_approval_cancels_request():
    reviews = [
        review("alice", "CHANGES_REQUESTED", "r1", "2026-01-01T00:00:00Z"),
        review("alice", "APPROVED", "r2", "2026-01-02T00:00:00Z"),
    ]
    assert not ev(pr(reviews))["eligible"]


def test_untrusted_reviewer_never_wakes():
    assert not ev(pr([review("mallory", "CHANGES_REQUESTED", "r1")]))["eligible"]


def test_failing_ci_wakes_unless_disabled():
    assert ev(pr(checks=[failing()]))["eligible"]
    assert not ev(pr(checks=[failing()]), settings={**SETTINGS, "ci": False})[
        "eligible"
    ]


def test_ci_still_running_does_not_wake():
    running = {
        "__typename": "CheckRun",
        "name": "slow",
        "status": "IN_PROGRESS",
        "conclusion": "",
    }
    assert not ev(pr(checks=[failing(), running]))["eligible"]


def test_ignored_check_does_not_wake():
    assert not ev(pr(checks=[failing("Assign labels")]))["eligible"]


def test_draft_ci_is_ignored():
    assert not ev(pr(checks=[failing()], draft=True))["eligible"]


def test_answered_request_is_not_repeated():
    s = state(attempt("cr:alice:r1", "pushed"))
    assert not ev(pr([review("alice", "CHANGES_REQUESTED", "r1")]), s)["eligible"]


def test_new_review_after_push_is_answered():
    s = state(attempt("cr:alice:r1", "pushed"))
    v = ev(pr([review("alice", "CHANGES_REQUESTED", "r2")]), s)
    assert v["eligible"] and v["components"] == ["cr:alice:r2"]


def test_gives_up_after_unproductive_attempts():
    s = state(
        attempt("cr:alice:r1", "nochange", ago=3600 * 40),
        attempt("cr:alice:r1", "failed", ago=3600 * 30),
    )
    assert not ev(pr([review("alice", "CHANGES_REQUESTED", "r1")]), s)["eligible"]


def test_backs_off_after_a_failure():
    s = state(attempt("cr:alice:r1", "failed", ago=600))
    v = ev(pr([review("alice", "CHANGES_REQUESTED", "r1")]), s)
    assert not v["eligible"] and "cooling down" in v["why"]


def test_wakes_again_after_the_back_off():
    s = state(attempt("cr:alice:r1", "failed", ago=3 * 3600))
    assert ev(pr([review("alice", "CHANGES_REQUESTED", "r1")]), s)["eligible"]


def test_attempt_in_progress_blocks():
    s = state(attempt("cr:alice:r1", "started", ago=60))
    v = ev(pr([review("alice", "CHANGES_REQUESTED", "r1")]), s)
    assert not v["eligible"] and "in progress" in v["why"]


def test_killed_attempt_stops_blocking_after_the_timeout():
    s = state(
        attempt("cr:alice:r1", "started", ago=SETTINGS["timeoutSeconds"] + 3 * 3600)
    )
    assert ev(pr([review("alice", "CHANGES_REQUESTED", "r1")]), s)["eligible"]


def test_daily_cap_bounds_a_ci_that_goes_red_on_every_fix():
    # Every push produces a new head, so every failure is a new component; the
    # daily cap is what stops that loop.
    s = state(
        *[attempt(f"ci:h{i}:build", "pushed", ago=3600 * (i + 1)) for i in range(4)]
    )
    v = ev(pr(checks=[failing()], head="h99"), s)
    assert not v["eligible"] and "daily" in v["why"]


def test_weekly_cap():
    s = state(
        *[attempt(f"ci:h{i}:build", "pushed", ago=DAYS(i + 2)) for i in range(10)]
    )
    v = ev(pr(checks=[failing()], head="h99"), s)
    assert not v["eligible"] and "weekly" in v["why"]


def DAYS(n):
    return n * 24 * 3600 - 3600 * 12 + 1


def test_not_before_blocks():
    s = state()
    s["notBefore"] = NOW + 100
    assert not ev(pr([review("alice", "CHANGES_REQUESTED", "r1")]), s)["eligible"]
