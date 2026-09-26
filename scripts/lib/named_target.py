"""What a run's request names outright: a story id, or an issue or PR number."""

import re

_TARGET_REF_RE = re.compile(r"(?:issues?|pull)/(\d+)\b|#(\d+)\b")
_TARGET_STORY_RE = re.compile(r"\bUS-\d+\b", re.IGNORECASE)


def explicit_target(extra_prompt):
    """("story", "US-212"), ("ref", 613) for an issue or PR, or None.

    A bare number is deliberately not a target: "./run.sh tui 2 small ones"
    names nothing, and guessing wrong is exactly what this exists to avoid.
    """
    story = _TARGET_STORY_RE.search(extra_prompt or "")
    if story:
        return ("story", story.group(0).upper())
    ref = _TARGET_REF_RE.search(extra_prompt or "")
    if ref:
        return ("ref", int(ref.group(1) or ref.group(2)))
    return None


def describe_target(target):
    """A target as the operator typed it, for an error message."""
    kind, value = target
    return value if kind == "story" else f"#{value}"
