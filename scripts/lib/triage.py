"""The triage axes a backlog is ordered by.

Two scripts share this vocabulary. `add-backlog-to-prd.py` reads the axes off an
issue's labels into a story's `triage` block, and `select-task.py` orders
candidate work by them. They have to agree on the axis names, the range, and
what an unjudged axis means, so all three live here rather than twice.

What each value *means* is the project's to define, not this repo's: the
profile's `labels.axes` says which label prefix spells each axis, and the
project's own docs say when to reach for which value. A project that does not
label its issues this way defines no prefixes, and nothing here ever fires.
"""

# In reading order rather than alphabetical: importance orders the backlog,
# urgency interrupts that order, and size says what fits in the time available.
AXES = ("importance", "urgency", "size")

# 1 to 5 on every axis, where 1 is the most important, the most urgent, and the
# smallest. A value outside that range is somebody's typo, not a sixth level.
MIN_VALUE = 1
MAX_VALUE = 5

# A missing axis is not a low value. It means nobody has judged the issue yet,
# so it sorts as the middle of the range instead of first or last -- an
# unjudged issue should neither jump the queue nor be buried by it.
NEUTRAL = 3.0


def axis_prefixes(profile):
    """Label prefix per axis, from the profile's `labels.axes`.

    Axes the profile leaves blank are dropped, so a half-filled mapping labels
    what it names and ignores the rest.
    """
    prefixes = (profile.get("labels") or {}).get("axes") or {}
    return {axis: prefix for axis, prefix in prefixes.items() if prefix}


def read_labels(label_names, prefixes):
    """The axes these label names spell, as {axis: 1..5}, unlabelled ones absent.

    A missing axis is left out rather than defaulted: nobody having judged the
    issue is not the same claim as judging it middling, and only the ordering
    decides what to do about the difference.

    Duplicates are not supposed to happen -- the axes are ordinary GitHub
    labels, so replacing a value means removing the old one in the same call --
    but where both survive the most severe (lowest) wins, so the order the API
    happened to list them in cannot change what gets worked on.
    """
    triage = {}
    for axis, prefix in prefixes.items():
        for name in label_names:
            if not name.lower().startswith(prefix.lower()):
                continue
            digits = name[len(prefix) :]
            if not digits.isdigit():
                continue
            value = int(digits)
            if MIN_VALUE <= value <= MAX_VALUE:
                triage[axis] = min(triage.get(axis, MAX_VALUE), value)
    return triage


def value(raw):
    """One axis value as a float, or NEUTRAL when it is missing or nonsense.

    `data/prd.json` is hand-editable, so a story can carry anything at all
    here; an unreadable axis has to sort like an unjudged one rather than
    raising in the middle of a run.
    """
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return NEUTRAL
    if number < MIN_VALUE or number > MAX_VALUE:
        return NEUTRAL
    return number


def rank(triage):
    """A story's `triage` block as the (urgency, importance) it sorts on.

    Urgency comes first because that is what the axis is for: importance orders
    the backlog and urgency interrupts that order. Size is deliberately not in
    the key -- it says what fits in the time available, and letting it order the
    queue would bury exactly the large, important work that needs splitting.
    """
    triage = triage or {}
    return (value(triage.get("urgency")), value(triage.get("importance")))


def format_triage(triage):
    """A triage block as "importance 2, urgency 3, size 2"; "" when empty.

    Axis order is AXES, not the block's own key order, so two stories always
    read the same way.
    """
    triage = triage or {}
    named = [f"{axis} {triage[axis]}" for axis in AXES if axis in triage]
    extra = [f"{axis} {triage[axis]}" for axis in sorted(triage) if axis not in AXES]
    return ", ".join(named + extra)
