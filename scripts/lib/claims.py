"""Story claims: which run is working which story.

Two runs sharing a bot directory must never select the same story. The PRD
cannot express that on its own — a story is "pending" whether or not somebody
is mid-iteration on it — so a run records a claim when it selects, and clears
it when the iteration ends.

A claim is *live* only while the run that took it still holds its slot lock
(see lib/slots.py). That makes crash recovery free: kill a run and the kernel
drops its lock, so its claims stop counting immediately. Nothing has to expire,
and there is no timeout to tune.

Claims live in data/claims.json, guarded by the same lock as the PRD, so
"pick a story and claim it" is one critical section.
"""

import json
import os

from .prd_store import prd_lock
from .slots import slot_is_running


def claims_path(bot_dir):
    return os.path.join(bot_dir, "data", "claims.json")


def _load(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path, claims):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(claims, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _live(bot_dir, claims):
    """Drop claims whose run is gone (or whose slot has since been reused)."""
    return {
        sid: c
        for sid, c in claims.items()
        if slot_is_running(bot_dir, c.get("slot"), c.get("pid"))
    }


def active(bot_dir, locked=False):
    """Claims held by runs that are still alive, keyed by story id."""
    path = claims_path(bot_dir)
    if locked:
        return _live(bot_dir, _load(path))
    with prd_lock(path):
        return _live(bot_dir, _load(path))


def claim(bot_dir, story_id, slot, pid, run_id=None, locked=False):
    """Record that this run is working ``story_id``.

    Returns True when the claim is ours. Returns False when another live run
    already holds it — the caller must then pick a different story.

    ``locked=True`` says the caller already holds the PRD lock (select-task.py
    does: it claims inside the same critical section it selects in). Taking it
    twice in one process would deadlock.
    """
    if locked:
        return _claim_unlocked(bot_dir, story_id, slot, pid, run_id)
    with prd_lock(claims_path(bot_dir)):
        return _claim_unlocked(bot_dir, story_id, slot, pid, run_id)


def _claim_unlocked(bot_dir, story_id, slot, pid, run_id):
    from datetime import datetime, timezone

    path = claims_path(bot_dir)
    claims = _live(bot_dir, _load(path))
    existing = claims.get(story_id)
    if existing and int(existing.get("slot", -1)) != int(slot):
        return False
    claims[story_id] = {
        "storyId": story_id,
        "slot": int(slot),
        "pid": int(pid),
        "runId": run_id,
        "claimedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save(path, claims)
    return True


def release(bot_dir, story_id=None, slot=None, locked=False):
    """Drop claims. Narrow by story, by slot, or both; neither means prune only.

    Every call also drops claims whose run is gone, so an ordinary release
    doubles as cleanup after a crash elsewhere.
    """
    if locked:
        return _release_unlocked(bot_dir, story_id, slot)
    with prd_lock(claims_path(bot_dir)):
        return _release_unlocked(bot_dir, story_id, slot)


def _release_unlocked(bot_dir, story_id, slot):
    path = claims_path(bot_dir)
    claims = _load(path)
    dropped = []
    for sid, c in list(claims.items()):
        if story_id is not None and sid != story_id:
            continue
        if slot is not None and int(c.get("slot", -1)) != int(slot):
            continue
        dropped.append(sid)
        del claims[sid]
    # Anything whose run died is cleaned up in the same pass.
    for sid in list(claims):
        if not slot_is_running(
            bot_dir, claims[sid].get("slot"), claims[sid].get("pid")
        ):
            dropped.append(sid)
            del claims[sid]
    if dropped:
        _save(path, claims)
    return dropped
