"""Clustering: how many related issues one iteration may take together.

A profile opts in with a `clustering` block in profile.json:

    "clustering": {"sizeLimits": {"1": 10, "2": 6, "3": 4, "4": 1, "5": 1}}

The key is the `size` triage axis of the story the iteration is *led* by (see
lib/triage.py), the value the most stories that iteration may work in one pull
request, the lead included. It is a ceiling, never a target: whether two issues
are related is a judgement that needs the issues read, which belongs to the
agent (projects/bravebot/docs/clustering.md), and a lead with nothing related
is worked alone.

This module decides only the two things a script can decide from the PRD: the
ceiling, and which other stories the agent may be offered as companions. A
profile with no `clustering` block gets a ceiling of 1 and no companions, so
nothing changes for it.
"""

from . import triage
from .issue_lock import issue_number

# How many companions are listed for the agent to choose from. The ceiling bounds
# how many it may take; the pool is wider than that so that a lead whose related
# issues are not the next ones in the queue still finds them. It is also a bound
# on what the prompt carries.
POOL_CAP = 40


def size_limits(profile):
    """The profile's {size: ceiling} as ints, or {} when it does not cluster.

    A malformed entry is dropped rather than raised: a bad number in a profile
    must not stop a run, and dropping it falls back to working one story.
    """
    raw = (profile.get("clustering") or {}).get("sizeLimits") or {}
    limits = {}
    for key, count in raw.items():
        try:
            size, count = int(key), int(count)
        except (TypeError, ValueError):
            continue
        if triage.MIN_VALUE <= size <= triage.MAX_VALUE and count >= 1:
            limits[size] = count
    return limits


def lead_size(story):
    """The lead's size label as an int, or None when it has none.

    Unlike ordering, where a missing axis is the middle of the range, an
    unlabelled size is treated as unknown here: how much work it is decides how
    many can share an iteration, and guessing high would overload one.
    """
    raw = (story.get("triage") or {}).get("size")
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return None
    if triage.MIN_VALUE <= size <= triage.MAX_VALUE:
        return size
    return None


def ceiling(profile, story):
    """The most stories one iteration led by ``story`` may work, itself included."""
    size = lead_size(story)
    if size is None:
        return 1
    return size_limits(profile).get(size, 1)


def companion_pool(lead, ordered, limit, blocked_issues=()):
    """Stories the agent may add to ``lead``, in the order they were given.

    ``ordered`` is the candidate list already filtered (not claimed elsewhere, not
    checked this run, not terminal) and sorted the way selection sorts it. From it
    this keeps what could share a pull request with the lead:

    - pending, and not quarantined (the caller passes the quarantine test);
    - it names an issue, since a story with none cannot be closed by a pull request;
    - its issue is not in progress on another machine;
    - its size is labelled and no larger than the lead's, so the lead's size is the
      size of the biggest thing in the cluster and the ceiling means what it says.

    Returns [] when the ceiling is 1.
    """
    if limit <= 1:
        return []
    size = lead_size(lead)
    if size is None:
        return []
    blocked = set(blocked_issues)
    pool = []
    for story in ordered:
        if story.get("id") == lead.get("id"):
            continue
        if story.get("status", "pending") != "pending":
            continue
        number = issue_number(story)
        if not number or number in blocked:
            continue
        other = lead_size(story)
        if other is None or other > size:
            continue
        pool.append(story)
        if len(pool) >= POOL_CAP:
            break
    return pool


def pool_entry(story):
    """One companion as the agent is shown it: enough to judge relatedness."""
    return {
        "storyId": story.get("id"),
        "issueNumber": issue_number(story),
        "title": story.get("title", ""),
        "triage": story.get("triage") or {},
    }
