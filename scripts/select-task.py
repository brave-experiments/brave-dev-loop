#!/usr/bin/env python3
"""Select the next task to work on from prd.json using deterministic rules.

Reads prd.json and run-state.json, applies filtering and tier-based selection,
and outputs the selected story as JSON to stdout.

Selection is one critical section under the PRD lock: filter, sort, claim the
winner, record it. Runs sharing a bot directory therefore cannot pick the same
story — whoever gets the lock first claims it, and the next run filters it out.

Exit codes:
  0 - Story selected (JSON output on stdout)
  1 - No candidates remain (run complete)
  2 - Error
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import claims as claims_lib
from lib import slots, triage
from lib.prd_store import bot_dir_for, load_prd, prd_lock, save_prd

TIER_URGENT = 1  # pushed + lastActivityBy == "reviewer"
TIER_HIGH = 2  # committed
# 2.5 (float) is used only as an *effective* sort tier to reserve the first
# slot of a run for pending work — see sort_key(promote_pending=True). No story
# is ever assigned it by assign_tier.
TIER_PENDING_RESERVED = 2.5
TIER_STALE = 3  # pushed + lastActivityBy != "reviewer" + not checked in >1 day
TIER_NORMAL = 4  # pending
TIER_MEDIUM = 5  # pushed + lastActivityBy != "reviewer" + checked within last day
TIER_LOW = 6  # merged (needs recheck)
TIER_QUARANTINE = 7  # pending that has been retried >= MAX_PENDING_ATTEMPTS times

TIER_NAMES = {
    TIER_URGENT: "URGENT",
    TIER_HIGH: "HIGH",
    TIER_STALE: "STALE",
    TIER_NORMAL: "NORMAL",
    TIER_MEDIUM: "MEDIUM",
    TIER_LOW: "LOW",
    TIER_QUARANTINE: "QUARANTINE",
}

STALE_THRESHOLD_SECONDS = 86400  # 1 day

# A pending story selected this many times without ever leaving "pending" is
# treated as stuck and quarantined to the back of the queue, so it stops
# blocking fresh pending work (and stops burning a full iteration every run).
# It is not dropped — it is only reachable once nothing else is selectable.
MAX_PENDING_ATTEMPTS = 5

# Sentinel for missing timestamps (sorts before everything = oldest)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_iso(ts):
    """Parse an ISO timestamp string, returning EPOCH if missing/invalid."""
    if not ts:
        return EPOCH
    try:
        # Handle Z suffix
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return EPOCH


def now_iso():
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pending_attempts(story):
    """Number of times a pending story has been selected (its iterationLogs)."""
    return len(story.get("iterationLogs") or [])


def assign_tier(story, now=None):
    """Assign a priority tier to a story based on its status and lastActivityBy."""
    if now is None:
        now = datetime.now(timezone.utc)
    status = story.get("status", "pending")
    last_activity = story.get("lastActivityBy")

    if status == "pushed":
        if last_activity == "reviewer":
            return TIER_URGENT
        last_processed = parse_iso(story.get("lastProcessedDate"))
        if (now - last_processed).total_seconds() > STALE_THRESHOLD_SECONDS:
            return TIER_STALE
        return TIER_MEDIUM
    elif status == "committed":
        return TIER_HIGH
    elif status == "pending":
        if pending_attempts(story) >= MAX_PENDING_ATTEMPTS:
            return TIER_QUARANTINE
        return TIER_NORMAL
    elif status == "merged":
        return TIER_LOW
    # Shouldn't reach here after filtering, but default to NORMAL
    return TIER_NORMAL


def sort_key(story, now=None, promote_pending=False):
    """Generate a sort key for a story within its tier.

    The key is (effective_tier, urgency, importance, secondary, priority); all
    five are numeric so stories are never compared across incompatible types.

    - Pushed stories (tiers 1, 3, 5): sort by lastProcessedDate asc, then priority
    - Merged stories (tier 6): sort by nextMergedCheck asc, then priority
    - Pending stories: sort by the story's triage axes (see scripts/lib/triage.py),
      then by attempt count asc (fresh work before what keeps getting retried),
      then priority
    - Other stories: sort by priority only

    Only pending work ranks on the axes. Pushed and merged maintenance keeps its
    round-robin by date: those queues exist so that every open PR is looked at
    in turn, and ordering them by importance would leave the least important PR
    waiting for review forever. Both slots hold the neutral value there, which
    is also what a pending story with no axes at all gets, so a backlog nobody
    has labelled sorts exactly as it did before the axes existed.

    When ``promote_pending`` is True, un-quarantined pending work is lifted just
    above STALE/MEDIUM pushed-maintenance (but still below URGENT reviewer
    responses and ready-to-push committed stories). This reserves the first slot
    of a run for actually developing a fix instead of nudging stale PRs.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    tier = assign_tier(story, now)
    priority = story.get("priority", 999)
    status = story.get("status")

    eff_tier = tier
    if promote_pending and status == "pending" and tier == TIER_NORMAL:
        eff_tier = TIER_PENDING_RESERVED

    if status == "pending":
        urgency, importance = triage.rank(story.get("triage"))
    else:
        urgency, importance = triage.NEUTRAL, triage.NEUTRAL

    if status == "pushed":
        secondary = parse_iso(story.get("lastProcessedDate")).timestamp()
    elif status == "merged":
        secondary = parse_iso(story.get("nextMergedCheck")).timestamp()
    elif status == "pending":
        secondary = float(pending_attempts(story))
    else:
        secondary = 0.0

    return (eff_tier, urgency, importance, secondary, priority)


def filter_stories(stories, run_state, claimed=None):
    """Apply all filtering rules to get candidate stories.

    ``claimed`` is the set of story ids another live run is already working;
    they are not candidates for this one.
    """
    checked = set(run_state.get("storiesCheckedThisRun", []))
    claimed = set(claimed or ())
    skip_pushed = run_state.get("skipPushedTasks", False)
    enable_merge_backoff = run_state.get("enableMergeBackoff", True)
    merge_backoff_ids = run_state.get("mergeBackoffStoryIds")
    now = datetime.now(timezone.utc)

    candidates = []
    for story in stories:
        sid = story.get("id", "")
        status = story.get("status", "pending")

        # Filter 2.1: Terminal status exclusion
        if status in ("skipped", "invalid"):
            continue
        if status == "merged" and story.get("mergedCheckFinalState") is True:
            continue

        # Filter 2.2: Merged story backoff filtering
        if status == "merged":
            if not enable_merge_backoff:
                continue
            if merge_backoff_ids and sid not in merge_backoff_ids:
                continue
            next_check = story.get("nextMergedCheck")
            if next_check:
                next_check_dt = parse_iso(next_check)
                if next_check_dt > now:
                    continue

        # Filter 2.3: Run state filtering
        if sid in checked:
            continue
        if sid in claimed:
            continue
        if skip_pushed and status == "pushed":
            continue

        candidates.append(story)

    return candidates


def candidate_summary(candidates):
    """One line per candidate for the LLM selector.

    Includes the PR and issue numbers so a bare number in the user's request
    ("./run.sh tui 38869") can be matched — a story's title never contains them.
    The triage axes are there so a request phrased as one ("the urgent ones",
    "something small") has something to match on.
    """
    summary_lines = []
    for s in candidates:
        refs = ""
        pr_number = s.get("prNumber")
        if pr_number:
            refs += f", PR #{pr_number}"
        issue_match = re.search(r"issue #(\d+)", s.get("description") or "")
        if issue_match:
            refs += f", issue #{issue_match.group(1)}"
        axes = triage.format_triage(s.get("triage"))
        if axes:
            refs += f", {axes}"
        summary_lines.append(
            f'- {s.get("id")}: "{s.get("title")}" '
            f"(status: {s.get('status')}, priority: {s.get('priority')}{refs})"
        )
    return "\n".join(summary_lines)


def llm_select(candidates, extra_prompt, claude_bin="claude"):
    """Use Claude CLI (haiku) to interpret extra_prompt and select a story."""
    summary = candidate_summary(candidates)

    prompt = (
        f"Given these candidate stories:\n{summary}\n\n"
        f"And this user request: {extra_prompt}\n\n"
        f"Which story should be selected? Reply with ONLY the story ID "
        f"(e.g., US-003). Nothing else."
    )

    try:
        result = subprocess.run(
            [claude_bin, "--print", "--model", "haiku", prompt],
            capture_output=True,
            text=True,
            timeout=30,
        )
        response = result.stdout.strip()
        print(f"LLM selection response: {response}", file=sys.stderr)
        # Extract US-XXX pattern from response
        match = re.search(r"US-\d+", response)
        if match:
            selected_id = match.group(0)
            valid_ids = {s.get("id") for s in candidates}
            if selected_id in valid_ids:
                return selected_id
            else:
                print(
                    f"LLM selected {selected_id} but it's not a valid candidate",
                    file=sys.stderr,
                )
        else:
            print("LLM response didn't contain a story ID", file=sys.stderr)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"LLM selection failed: {e}", file=sys.stderr)

    return None


def update_run_state(run_state_path, run_state, story_id):
    """Add story ID to storiesCheckedThisRun in run-state.json."""
    checked = run_state.get("storiesCheckedThisRun", [])
    if story_id not in checked:
        checked.append(story_id)
    run_state["storiesCheckedThisRun"] = checked
    save_prd(run_state_path, run_state)  # same atomic replace, any JSON file


def update_prd(prd_path, prd, story, iteration_log):
    """Update story in prd.json: lastProcessedDate, iterationLogs."""
    # Set lastProcessedDate for pushed stories
    if story.get("status") == "pushed":
        story["lastProcessedDate"] = now_iso()

    # Append iteration log path
    if iteration_log:
        logs = story.get("iterationLogs", [])
        logs.append(iteration_log)
        story["iterationLogs"] = logs

    save_prd(prd_path, prd)


def main():
    parser = argparse.ArgumentParser(description="Select next task from prd.json")
    parser.add_argument("--prd", help="Path to prd.json")
    parser.add_argument("--run-state", help="Path to run-state.json")
    parser.add_argument("--iteration-log", help="Log path to record in story")
    parser.add_argument(
        "--extra-prompt", default="", help="Extra prompt for LLM selection"
    )
    parser.add_argument(
        "--claude-bin", default="claude", help="Path to claude CLI binary"
    )
    parser.add_argument(
        "--slot",
        type=int,
        default=int(os.environ.get("BOT_RUN_SLOT", "1")),
        help="Run slot this selection belongs to (default: $BOT_RUN_SLOT, or 1)",
    )
    parser.add_argument(
        "--run-pid",
        type=int,
        default=int(os.environ.get("BOT_RUN_PID", "0")) or os.getppid(),
        help="pid of the run.sh that owns the slot (default: $BOT_RUN_PID)",
    )
    parser.add_argument("--run-id", default=os.environ.get("BOT_RUN_ID", ""))
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    bot_dir = os.path.dirname(script_dir)

    prd_path = args.prd or os.path.join(bot_dir, "data", "prd.json")
    # Each slot has its own run state; slot 1 keeps the original path.
    run_state_path = (
        args.run_state
        or os.environ.get("BOT_RUN_STATE_FILE")
        or slots.slot_run_state_file(bot_dir, args.slot)
    )

    # Everything from here to the claim runs under the PRD lock, so a second
    # run cannot read the same candidate list and pick the same story. The
    # LLM call for --extra-prompt happens inside it too: it costs up to 30s of
    # another run's waiting, which is cheaper than selecting against a story
    # list that went stale while we were asking.
    # Claims and slot locks belong to the bot directory that owns this PRD,
    # which is not necessarily the one this script lives in (tests, or a --prd
    # pointing somewhere else).
    bot_dir = bot_dir_for(prd_path, bot_dir)

    try:
        with prd_lock(prd_path):
            return _select_locked(args, prd_path, run_state_path, bot_dir)
    except TimeoutError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


def _select_locked(args, prd_path, run_state_path, bot_dir):
    # Read prd.json
    try:
        prd = load_prd(prd_path)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading prd.json: {e}", file=sys.stderr)
        return 2

    # Read run-state.json
    try:
        with open(run_state_path) as f:
            run_state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading run-state.json: {e}", file=sys.stderr)
        return 2

    stories = prd.get("stories", [])
    if not stories:
        print(json.dumps({"selected": False, "reason": "No stories in prd.json"}))
        return 1

    # Stories other live runs are working. Ours (same slot) are not excluded:
    # a claim left by our own previous iteration is ours to take again.
    held = claims_lib.active(bot_dir, locked=True)
    claimed_elsewhere = {
        sid for sid, c in held.items() if int(c.get("slot", -1)) != int(args.slot)
    }

    # Apply filters
    candidates = filter_stories(stories, run_state, claimed=claimed_elsewhere)

    if not candidates:
        reason = "No candidates remain after filtering"
        if claimed_elsewhere:
            reason += (
                f" ({len(claimed_elsewhere)} claimed by other runs: "
                f"{', '.join(sorted(claimed_elsewhere))})"
            )
        print(json.dumps({"selected": False, "reason": reason}))
        return 1

    # Selection
    selected = None

    # Try LLM-assisted selection if extra prompt provided
    if args.extra_prompt.strip():
        # Use all non-terminal stories for LLM selection so the user can
        # override run-state filters (e.g. pick an already-checked story)
        all_active = [
            s
            for s in stories
            if s.get("status") not in ("skipped", "invalid")
            and s.get("id") not in claimed_elsewhere
            and not (
                s.get("status") == "merged" and s.get("mergedCheckFinalState") is True
            )
        ]
        llm_choice = llm_select(
            all_active, args.extra_prompt, claude_bin=args.claude_bin
        )
        if llm_choice:
            selected = next((s for s in all_active if s.get("id") == llm_choice), None)
        if not selected:
            print(
                f"WARNING: could not match extra prompt {args.extra_prompt!r} to a "
                f"story; falling back to deterministic tier selection",
                file=sys.stderr,
            )

    # Fall back to deterministic tier-based selection
    if not selected:
        now = datetime.now(timezone.utc)
        # Reserve the first pick of each run for pending fix development so
        # stale-PR maintenance can't consume every slot. run-state resets at the
        # start of a run, so an empty storiesCheckedThisRun means this is slot 1.
        reserve_pending = len(run_state.get("storiesCheckedThisRun", [])) == 0
        candidates.sort(key=lambda s: sort_key(s, now, reserve_pending))
        selected = candidates[0]

    now = datetime.now(timezone.utc)
    story_id = selected.get("id", "?")
    status = selected.get("status", "pending")
    tier = assign_tier(selected, now)

    # Claim it while we still hold the lock, so no other run can select it.
    # Filtering already excluded stories held elsewhere; a refusal here means
    # the state changed under us, so take the next candidate rather than
    # working a story someone else is on.
    if not claims_lib.claim(
        bot_dir, story_id, args.slot, args.run_pid, args.run_id or None, locked=True
    ):
        remaining = [c for c in candidates if c.get("id") != story_id]
        if not remaining:
            print(
                json.dumps(
                    {
                        "selected": False,
                        "reason": f"{story_id} was claimed by another run",
                    }
                )
            )
            return 1
        selected = remaining[0]
        story_id = selected.get("id", "?")
        status = selected.get("status", "pending")
        tier = assign_tier(selected, now)
        if not claims_lib.claim(
            bot_dir, story_id, args.slot, args.run_pid, args.run_id or None, locked=True
        ):
            print(
                json.dumps(
                    {"selected": False, "reason": "Could not claim any candidate"}
                )
            )
            return 1

    # Update run-state.json
    update_run_state(run_state_path, run_state, story_id)

    # Update prd.json
    update_prd(prd_path, prd, selected, args.iteration_log)

    # Output result with full story details embedded in prompt
    result = {
        "selected": True,
        "storyId": story_id,
        "status": status,
        "tier": tier,
        "tierName": TIER_NAMES.get(tier, "UNKNOWN"),
        "title": selected.get("title", ""),
        "priority": selected.get("priority"),
        "candidateCount": len(candidates),
        "slot": args.slot,
        "storyDetails": selected,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
