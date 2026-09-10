#!/usr/bin/env python3
"""Update prd.json story fields via deterministic CLI commands.

Replaces manual LLM-driven prd.json edits with atomic, validated updates.
Each subcommand handles a specific status transition or field update.

Read, validate, change, write is one critical section under the PRD lock
(lib/prd_store.py). Without it, two runs updating different stories at the
same moment each write back the whole file and the slower one erases the
other's change.

Exit codes:
  0 - Update succeeded
  1 - Validation error (illegal transition, missing field, story not found)
  2 - I/O or parse error
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.load_config import load_config, require_config
from lib.prd_store import prd_lock

TERMINAL_STATUSES = {"skipped", "invalid"}

# Subcommands that represent a state change (for run-state.json tracking)
STATUS_CHANGE_SUBCOMMANDS = {"committed", "pushed", "merged", "skipped", "invalid"}

# Valid current statuses for each subcommand (None = any non-terminal)
VALID_STATUSES = {"pending", "committed", "pushed", "merged", "skipped", "invalid"}

VALID_CURRENT_STATUSES = {
    "committed": {"pending"},
    "pushed": {"committed"},
    "merged": {"pushed"},
    "skipped": None,
    "invalid": None,
    "set-activity": {"pushed"},
    "set-ping": {"pushed"},
    "set-escalation": {"pushed"},
    "set-branch": {"pending", "committed"},
    "merged-check": {"merged"},
    "fix-status": None,  # any -> valid status
}


def now_iso():
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    """Load and return parsed JSON from path."""
    with open(path) as f:
        return json.load(f)


def atomic_write_json(path, data):
    """Write JSON to path atomically via temp file + rename."""
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def find_story(prd, story_id):
    """Find and return story dict by ID, or None."""
    for story in prd.get("stories", []):
        if story.get("id") == story_id:
            return story
    return None


def validate_transition(subcommand, story):
    """Validate the transition is legal. Returns error string or None."""
    story_id = story.get("id", "?")
    current_status = story.get("status", "pending")
    allowed = VALID_CURRENT_STATUSES.get(subcommand)

    if subcommand == "fix-status":
        # fix-status: allowed from any status (validation in handler)
        return None

    if allowed is None:
        # skipped/invalid: allowed from any non-terminal status
        if current_status in TERMINAL_STATUSES:
            return (
                f"Cannot transition {story_id} to '{subcommand}': "
                f"current status '{current_status}' is already terminal"
            )
        return None

    if current_status not in allowed:
        return (
            f"Cannot apply '{subcommand}' to {story_id}: "
            f"current status is '{current_status}', "
            f"expected one of {sorted(allowed)}"
        )

    if subcommand == "merged-check":
        if story.get("mergedCheckFinalState") is True:
            return (
                f"Cannot run merged-check on {story_id}: "
                f"mergedCheckFinalState is already true"
            )

    return None


def update_run_state_flag(run_state_path, had_state_change):
    """Set lastIterationHadStateChange in run-state.json."""
    try:
        run_state = load_json(run_state_path)
    except (OSError, json.JSONDecodeError):
        return
    run_state["lastIterationHadStateChange"] = had_state_change
    atomic_write_json(run_state_path, run_state)


def is_state_change(subcommand, args):
    """Return True if this subcommand represents a state change."""
    if subcommand in STATUS_CHANGE_SUBCOMMANDS:
        return True
    if subcommand == "set-activity" and getattr(args, "who", None) == "bot":
        return True
    return False


# --- Subcommand handlers ---
# Each returns a dict of {field: new_value} and mutates story in-place.


def handle_committed(story, args):
    changes = {}
    story["status"] = "committed"
    changes["status"] = "committed"
    story["lastActivityBy"] = None
    changes["lastActivityBy"] = None
    story["branchName"] = args.branch
    changes["branchName"] = args.branch
    return changes


def handle_pushed(story, args):
    config = load_config()
    pr_repo = require_config(config, "project.prRepository")
    pr_number = args.pr_number
    pr_url = f"https://github.com/{pr_repo}/pull/{pr_number}"
    changes = {}
    story["status"] = "pushed"
    changes["status"] = "pushed"
    story["prNumber"] = pr_number
    changes["prNumber"] = pr_number
    story["prUrl"] = pr_url
    changes["prUrl"] = pr_url
    story["lastActivityBy"] = "bot"
    changes["lastActivityBy"] = "bot"
    return changes


def handle_merged(story, args):
    now = now_iso()
    now_dt = datetime.now(timezone.utc)
    next_check = (now_dt + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    changes = {}
    story["status"] = "merged"
    changes["status"] = "merged"
    story["mergedAt"] = now
    changes["mergedAt"] = now
    story["nextMergedCheck"] = next_check
    changes["nextMergedCheck"] = next_check
    story["mergedCheckCount"] = 0
    changes["mergedCheckCount"] = 0
    story["mergedCheckFinalState"] = False
    changes["mergedCheckFinalState"] = False
    return changes


def handle_skipped(story, args):
    changes = {}
    story["status"] = "skipped"
    changes["status"] = "skipped"
    story["skipReason"] = args.reason
    changes["skipReason"] = args.reason
    return changes


def handle_invalid(story, args):
    changes = {}
    story["status"] = "invalid"
    changes["status"] = "invalid"
    story["skipReason"] = args.reason
    changes["skipReason"] = args.reason
    return changes


def handle_set_activity(story, args):
    changes = {}
    story["lastActivityBy"] = args.who
    changes["lastActivityBy"] = args.who
    return changes


def handle_set_ping(story, args):
    now = now_iso()
    changes = {}
    story["lastReviewerPing"] = now
    changes["lastReviewerPing"] = now
    count = story.get("reviewerPingCount", 0) + 1
    story["reviewerPingCount"] = count
    changes["reviewerPingCount"] = count
    return changes


def handle_set_escalation(story, args):
    now = now_iso()
    changes = {}
    story["lastOwnerEscalation"] = now
    changes["lastOwnerEscalation"] = now
    count = story.get("ownerEscalationCount", 0) + 1
    story["ownerEscalationCount"] = count
    changes["ownerEscalationCount"] = count
    return changes


def handle_set_branch(story, args):
    changes = {}
    story["branchName"] = args.branch
    changes["branchName"] = args.branch
    return changes


def handle_merged_check(story, args):
    """Increment mergedCheckCount and recalculate nextMergedCheck.

    Backoff schedule (based on current count before increment):
      count 0 -> next in 2 days
      count 1 -> next in 4 days
      count 2 -> next in 8 days
      count >= 3 -> final state (no more checks)
    """
    count = story.get("mergedCheckCount", 0)
    now_dt = datetime.now(timezone.utc)
    changes = {}

    intervals = {0: 2, 1: 4, 2: 8}

    if count >= 3:
        story["mergedCheckFinalState"] = True
        changes["mergedCheckFinalState"] = True
        story["nextMergedCheck"] = None
        changes["nextMergedCheck"] = None
    else:
        days = intervals[count]
        next_check = (now_dt + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        story["nextMergedCheck"] = next_check
        changes["nextMergedCheck"] = next_check

    new_count = count + 1
    story["mergedCheckCount"] = new_count
    changes["mergedCheckCount"] = new_count

    return changes


def handle_fix_status(story, args):
    old_status = story.get("status", "unknown")
    if old_status in VALID_STATUSES:
        # Refuse to "fix" a status that's already valid
        print(
            f"Status '{old_status}' is already valid. "
            f"Use the appropriate subcommand for transitions.",
            file=sys.stderr,
        )
        sys.exit(1)
    changes = {}
    story["status"] = args.target_status
    changes["status"] = f"{old_status} -> {args.target_status}"
    return changes


HANDLER_MAP = {
    "committed": handle_committed,
    "pushed": handle_pushed,
    "merged": handle_merged,
    "skipped": handle_skipped,
    "invalid": handle_invalid,
    "set-activity": handle_set_activity,
    "set-ping": handle_set_ping,
    "set-escalation": handle_set_escalation,
    "set-branch": handle_set_branch,
    "merged-check": handle_merged_check,
    "fix-status": handle_fix_status,
}


def main():
    parser = argparse.ArgumentParser(
        description="Update prd.json story fields deterministically"
    )
    parser.add_argument("--prd", help="Path to prd.json")
    parser.add_argument("--run-state", help="Path to run-state.json")

    sub = parser.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("committed", help="pending -> committed")
    p.add_argument("story_id")
    p.add_argument("--branch", required=True)

    p = sub.add_parser("pushed", help="committed -> pushed")
    p.add_argument("story_id")
    p.add_argument("--pr-number", type=int, required=True)

    p = sub.add_parser("merged", help="pushed -> merged")
    p.add_argument("story_id")

    p = sub.add_parser("skipped", help="any -> skipped")
    p.add_argument("story_id")
    p.add_argument("--reason", required=True)

    p = sub.add_parser("invalid", help="any -> invalid")
    p.add_argument("story_id")
    p.add_argument("--reason", required=True)

    p = sub.add_parser("set-activity", help="Set lastActivityBy")
    p.add_argument("story_id")
    p.add_argument("--who", required=True, choices=["bot", "reviewer"])

    p = sub.add_parser(
        "set-ping", help="Set lastReviewerPing to now and increment reviewerPingCount"
    )
    p.add_argument("story_id")

    p = sub.add_parser(
        "set-escalation",
        help="Set lastOwnerEscalation to now and increment ownerEscalationCount",
    )
    p.add_argument("story_id")

    p = sub.add_parser("set-branch", help="Set branchName")
    p.add_argument("story_id")
    p.add_argument("--branch", required=True)

    p = sub.add_parser("merged-check", help="Post-merge check update")
    p.add_argument("story_id")

    p = sub.add_parser("fix-status", help="Fix invalid status to a valid one")
    p.add_argument("story_id")
    p.add_argument(
        "--target",
        required=True,
        dest="target_status",
        choices=sorted(VALID_STATUSES),
        help="Target valid status",
    )

    args = parser.parse_args()

    # Resolve paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bot_dir = os.path.dirname(script_dir)
    prd_path = args.prd or os.path.join(bot_dir, "data", "prd.json")
    # Run state is per run slot; BOT_RUN_STATE_FILE is exported by run.sh so
    # an agent in slot 2 cannot write slot 1's iteration bookkeeping.
    run_state_path = (
        args.run_state
        or os.environ.get("BOT_RUN_STATE_FILE")
        or os.path.join(bot_dir, "data", "run-state.json")
    )

    try:
        with prd_lock(prd_path):
            # Load prd.json
            try:
                prd = load_json(prd_path)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Error reading prd.json: {e}", file=sys.stderr)
                return 2

            # Find story
            story = find_story(prd, args.story_id)
            if story is None:
                print(
                    f"Error: Story '{args.story_id}' not found in prd.json",
                    file=sys.stderr,
                )
                return 1

            # Validate transition
            error = validate_transition(args.subcommand, story)
            if error:
                print(f"Validation error: {error}", file=sys.stderr)
                return 1

            # Apply changes
            handler = HANDLER_MAP[args.subcommand]
            changes = handler(story, args)

            # Write prd.json atomically
            atomic_write_json(prd_path, prd)
    except TimeoutError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # Update run-state.json if this was a state change
    if is_state_change(args.subcommand, args):
        update_run_state_flag(run_state_path, True)

    # Print confirmation
    print(f"Updated {args.story_id}:")
    for key, value in changes.items():
        print(f"  {key}: {json.dumps(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
