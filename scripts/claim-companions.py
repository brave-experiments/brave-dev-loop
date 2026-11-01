#!/usr/bin/env python3
"""Claim companion stories for a clustered iteration.

select-task.py claims the lead story and, for a profile with a `clustering`
block, offers a pool of stories that could share the iteration (see
lib/cluster.py). The agent reads the issues, decides which are genuinely related
to the lead, and asks for them here. Related is a judgement about the issues'
text and code, so it stays with the agent; the ceiling, the claims and the
labels do not, so they are enforced here, under the PRD lock, where a model
cannot talk its way past them.

  ./scripts/claim-companions.py --lead US-012 US-015 US-031

A companion is claimed exactly like the lead: the same slot, pid and run, so it
is released with the lead when the iteration ends (run.sh releases by slot), and
a run elsewhere cannot select it. It is also recorded as checked this run.

A story that cannot be taken is refused with its reason and the rest are still
claimed. Only a lead this slot does not hold fails the call.

Prints {"claimed": [...], "refused": {id: reason}, "limit": N, "taken": N}.

Exit codes:
  0 - the call was valid (check "claimed": it may be empty)
  1 - the lead is not claimed by this slot, or the profile does not cluster it
  2 - error
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import claims as claims_lib
from lib import cluster, issue_lock
from lib.issue_lock import issue_number
from lib.load_config import load_config, load_profile
from lib.prd_store import bot_dir_for, load_prd, prd_lock, save_prd


def _record_checked(run_state_path, story_ids):
    """Add the companions to storiesCheckedThisRun, as selection does for the lead."""
    try:
        with open(run_state_path) as f:
            run_state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    checked = run_state.get("storiesCheckedThisRun", [])
    for sid in story_ids:
        if sid not in checked:
            checked.append(sid)
    run_state["storiesCheckedThisRun"] = checked
    save_prd(run_state_path, run_state)


def claim_companions(bot_dir, prd_path, lead_id, wanted, slot, profile, in_progress):
    """Claim what can be claimed. Returns (result, exit_code); under the PRD lock."""
    prd = load_prd(prd_path)
    stories = prd.get("stories", [])
    by_id = {s.get("id"): s for s in stories}
    lead = by_id.get(lead_id)

    held = claims_lib.active(bot_dir, locked=True)
    lead_claim = held.get(lead_id)
    if lead is None or lead_claim is None or int(lead_claim.get("slot", -1)) != slot:
        return {"error": f"{lead_id} is not claimed by slot {slot}"}, 1

    limit = cluster.ceiling(profile, lead)
    if limit <= 1:
        return {
            "error": f"the profile does not cluster {lead_id} "
            f"(size {cluster.lead_size(lead)})"
        }, 1

    already = [sid for sid in lead.get("clusterCompanions", []) if sid in by_id]
    claimed, refused = [], {}
    # Never the labels this machine's own claims put on: those are not another
    # machine's work. Same carve-out select-task.py makes.
    blocked = set(in_progress) - {
        issue_number(s) for s in stories if s.get("id") in held
    }

    for sid in dict.fromkeys(wanted):
        if sid == lead_id or sid in already:
            refused[sid] = "already in this cluster"
            continue
        story = by_id.get(sid)
        if story is None:
            refused[sid] = "no such story"
            continue
        if 1 + len(already) + len(claimed) >= limit:
            refused[sid] = (
                f"the ceiling for a size-{cluster.lead_size(lead)} lead is {limit}"
            )
            continue
        # One story, judged with the same rules the pool was built with.
        if not cluster.companion_pool(lead, [story], limit, blocked):
            refused[sid] = (
                "not pending, has a PR, names no issue, is unlabelled or larger "
                "than the lead, or its issue is in progress elsewhere"
            )
            continue
        other = held.get(sid)
        if other and int(other.get("slot", -1)) != slot:
            refused[sid] = f"another run holds it (slot {other.get('slot')})"
            continue
        if not claims_lib.claim(
            bot_dir,
            sid,
            slot,
            lead_claim.get("pid", 0),
            lead_claim.get("runId"),
            locked=True,
        ):
            refused[sid] = "another run claimed it just now"
            continue
        if not issue_lock.acquire(issue_number(story), bot_dir=bot_dir):
            claims_lib.release(bot_dir, story_id=sid, slot=slot, locked=True)
            refused[sid] = "its issue was labelled in progress by another machine"
            continue
        claimed.append(sid)

    if claimed:
        lead["clusterCompanions"] = already + claimed
        for sid in claimed:
            by_id[sid]["clusterLead"] = lead_id
        save_prd(prd_path, prd)

    return {
        "claimed": claimed,
        "refused": refused,
        "limit": limit,
        "taken": 1 + len(already) + len(claimed),
    }, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--prd", help="Path to prd.json")
    parser.add_argument("--run-state", help="Path to run-state.json")
    parser.add_argument("--lead", required=True, help="The story this iteration leads")
    parser.add_argument(
        "--slot",
        type=int,
        default=int(os.environ.get("BOT_RUN_SLOT", "1")),
    )
    parser.add_argument("stories", nargs="+", help="Companion story ids")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    prd_path = args.prd or os.path.join(os.path.dirname(script_dir), "data", "prd.json")
    bot_dir = bot_dir_for(prd_path, os.path.dirname(script_dir))
    run_state_path = (
        args.run_state
        or os.environ.get("BOT_RUN_STATE_FILE")
        or os.path.join(bot_dir, "data", "run-state.json")
    )

    profile = load_profile(load_config(), bot_dir)
    wanted = [s.upper() for s in args.stories]
    # Read before the lock, as select-task.py does: it is a network call.
    in_progress = issue_lock.in_progress(bot_dir=bot_dir)

    try:
        with prd_lock(prd_path):
            result, code = claim_companions(
                bot_dir,
                prd_path,
                args.lead.upper(),
                wanted,
                args.slot,
                profile,
                in_progress,
            )
    except (OSError, ValueError, TimeoutError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if result.get("claimed"):
        _record_checked(run_state_path, result["claimed"])
    print(json.dumps(result))
    return code


if __name__ == "__main__":
    sys.exit(main())
