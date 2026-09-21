"""A claim on an issue that another machine can see, held as a label.

data/claims.json keeps two runs in one bot directory off the same story. It
cannot see a second machine: several deployments each have their own claims
file and their own PRD, and every one of them reads the same GitHub backlog —
so all of them select the same issue and open the same fix more than once. A
label on the issue is the one piece of claim state every machine reads.

The label mirrors the claim. select-task.py puts it on when it claims a story,
claims.py takes it off when the claim is handed back at the end of an iteration,
and update-prd-status.py takes it off as soon as a story is finished with. No
agent is involved in any of it.

What the label cannot borrow from a claim is the kernel. A claim counts only
while its run holds a slot lock, so killing a run frees its stories at once —
but a lock on one machine says nothing to the other two, and a machine that
dies never removes its labels. run.sh therefore drops the labels older than
the age limit at start: that is the whole reason the age limit exists, and why
it can be generous rather than tuned.

The label speaks for other machines only. Where this machine's claims file has
something to say about a story, that wins: a label is a weaker claim than the
lock-backed one beside it, and must never outvote it — see select-task.py.

Every call here is best-effort. A GitHub hiccup must not stop a run that can
still work its own PRD, so a failed read means "no cross-machine guard for this
selection" (what the loop did before the label existed) and never "every issue
is blocked".
"""

import json
import re
import subprocess  # nosemgrep
import sys
from datetime import datetime, timedelta, timezone

from .load_config import get_config, load_config, load_profile, require_config

# How long a label stands before a run at start treats it as abandoned. Only a
# killed machine ever leaves one that long — an iteration is capped at 2h by
# timeout-tree.sh and hands its story back at the end — so the limit is well
# clear of any live run, and still short enough that a dead machine does not
# park an issue for a day.
DEFAULT_MAX_AGE_HOURS = 6

_ISSUE_RE = re.compile(r"issue #(\d+)")


def issue_number(story):
    """Issue number a story references in its description, or None.

    Stories carry the issue only in their prose ("Resolve issue #133"), so the
    number has to be parsed back out whenever something wants to link to it.
    """
    match = _ISSUE_RE.search(story.get("description") or "")
    return int(match.group(1)) if match else None


def _config(config=None):
    return config if config is not None else load_config()


def label_name(config=None, bot_dir=None):
    """This project's in-progress label, or "" where it has none.

    The profile owns it, because the label has to exist in that project's issue
    repository and be spelled the way that repository spells its bot labels;
    `labels.inProgressLabel` in config.json overrides it for one deployment.
    Empty turns the cross-machine guard off, which is the right answer for a
    project only ever run from one machine.
    """
    config = _config(config)
    from_profile = (load_profile(config, bot_dir).get("labels") or {}).get("inProgress")
    if from_profile:
        return from_profile.strip()
    return (get_config(config, "labels.inProgressLabel", "") or "").strip()


def _gh(args, timeout=120):
    """Run gh, returning stdout — or None, having warned, on any failure."""
    try:
        result = subprocess.run(
            ["gh"] + args, capture_output=True, text=True, timeout=timeout
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        print(f"WARNING: gh {' '.join(args)}: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(
            f"WARNING: gh {' '.join(args)}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    return result.stdout


def _parse_iso(ts):
    """Parse a GitHub timestamp, or None if it is not one."""
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None


def labels_on(issue, config=None):
    """Every label on one issue, or None if it could not be read.

    `gh issue view` reads the issue. `gh issue list --label` searches an index
    that trails a write by a few seconds (measured: 4.8s to show a label that
    had been added, 2.2s to stop showing one that had been removed). So this is
    the read to trust when the answer decides something.
    """
    config = _config(config)
    out = _gh(
        [
            "issue",
            "view",
            str(issue),
            "--repo",
            require_config(config, "project.issueRepository"),
            "--json",
            "labels",
            "--jq",
            "[.labels[].name]",
        ]
    )
    if out is None:
        return None
    try:
        return set(json.loads(out))
    except (TypeError, ValueError, json.JSONDecodeError) as e:
        print(f"WARNING: could not read the labels of #{issue}: {e}", file=sys.stderr)
        return None


def in_progress(config=None, bot_dir=None):
    """Issue numbers labelled in progress, whichever machine labelled them.

    Deliberately not filtered by assignee: the label means somebody is working
    the issue, and that is true whoever it is assigned to. `stale()` is the one
    that narrows to the bot's own issues, because that one removes labels.

    This is the index-backed read, so it misses a label applied in the last few
    seconds. It is the right shape for filtering a whole PRD in one call, and
    `acquire()` closes the gap by reading the one issue it is about to take.
    """
    label = label_name(config, bot_dir)
    if not label:
        return set()
    config = _config(config)
    out = _gh(
        [
            "issue",
            "list",
            "--repo",
            require_config(config, "project.issueRepository"),
            "--label",
            label,
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            "200",
        ]
    )
    if not out:
        return set()
    try:
        return {int(issue["number"]) for issue in json.loads(out)}
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
        print(f"WARNING: could not read the {label} issue list: {e}", file=sys.stderr)
        return set()


def acquire(issue, config=None, bot_dir=None):
    """Take the in-progress label on an issue. False only if it is already taken.

    False means the label was on the issue when we looked, so a machine
    elsewhere is working it and the caller must pick something else. That is the
    one answer worth a second call: the filtering read searches an index that
    trails by seconds, and `make schedules` gives every machine the same cron
    times, so two of them select inside that window rather than by bad luck.

    Everything else is True, because nothing stands in the way of working the
    story: a project with no label configured, a story naming no issue, an
    unreadable issue, a failed write. A guard that cannot be placed costs the
    guard, not the work.
    """
    label = label_name(config, bot_dir)
    if not label or not issue:
        return True
    config = _config(config)
    held = labels_on(issue, config)
    if held is not None and label in held:
        return False
    _gh(
        [
            "issue",
            "edit",
            str(issue),
            "--repo",
            require_config(config, "project.issueRepository"),
            "--add-label",
            label,
        ]
    )
    return True


def release(issue, config=None, bot_dir=None):
    """Take the in-progress label off an issue. True when the call succeeded."""
    label = label_name(config, bot_dir)
    if not label or not issue:
        return False
    config = _config(config)
    return (
        _gh(
            [
                "issue",
                "edit",
                str(issue),
                "--repo",
                require_config(config, "project.issueRepository"),
                "--remove-label",
                label,
            ]
        )
        is not None
    )


def labelled_at(issue, label, config=None):
    """When ``label`` was last applied to ``issue``, or None if unreadable.

    The newest `labeled` event wins: a label taken off and put back on is a
    fresh claim, not the age of the first one.
    """
    config = _config(config)
    out = _gh(
        [
            "api",
            "--paginate",
            f"repos/{require_config(config, 'project.issueRepository')}"
            f"/issues/{issue}/events",
            "--jq",
            '.[] | select(.event == "labeled") | {name: .label.name, at: .created_at}',
        ]
    )
    if out is None:
        return None
    applied = []
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("name") == label:
            when = _parse_iso(event.get("at"))
            if when:
                applied.append(when)
    return max(applied) if applied else None


def stale(max_age_hours=DEFAULT_MAX_AGE_HOURS, config=None, bot_dir=None, now=None):
    """Issues whose in-progress label has stood longer than the age limit.

    Only issues assigned to the bot are considered. The label is the bot's own
    bookkeeping; the same name on somebody else's issue is a human saying
    something the loop has no business undoing.

    An issue whose label has no readable age counts as stale. The label is only
    ever meant to be a claim held for hours, so the failure to prefer is the one
    that hands the issue back — the other parks it in nobody's queue for good.
    """
    label = label_name(config, bot_dir)
    if not label:
        return []
    config = _config(config)
    out = _gh(
        [
            "issue",
            "list",
            "--repo",
            require_config(config, "project.issueRepository"),
            "--assignee",
            require_config(config, "bot.username"),
            "--label",
            label,
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            "200",
        ]
    )
    if not out:
        return []
    try:
        numbers = sorted(int(issue["number"]) for issue in json.loads(out))
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
        print(f"WARNING: could not read the {label} issue list: {e}", file=sys.stderr)
        return []

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=max_age_hours)
    expired = []
    for number in numbers:
        applied = labelled_at(number, label, config)
        if applied is None:
            print(
                f"WARNING: issue #{number} carries {label} with no readable "
                f"labelled date — treating it as abandoned.",
                file=sys.stderr,
            )
            expired.append(number)
        elif applied < cutoff:
            expired.append(number)
    return expired


def sweep(
    max_age_hours=DEFAULT_MAX_AGE_HOURS,
    config=None,
    bot_dir=None,
    now=None,
    dry_run=False,
):
    """Release every in-progress label past the age limit. Returns the issues."""
    released = []
    for issue in stale(max_age_hours, config, bot_dir, now):
        if dry_run or release(issue, config, bot_dir):
            released.append(issue)
    return released
