#!/usr/bin/env python3
"""Inspect and clear story claims.

A claim says "run in slot N is working this story", and stops a second run
from selecting the same one. Claims clear themselves when the run that took
them dies (see lib/claims.py), so this is for looking, and for the rare case
where you want to hand a story back by hand.

Handing a story back also takes the in-progress label off its issue, which is
the same claim as read by a run on another machine (see lib/issue_lock.py).
Both halves go together: a story nobody here is working must not look worked
to anybody else.

  ./scripts/claims.py list [--json]
  ./scripts/claims.py release --story US-004
  ./scripts/claims.py release --slot 2
  ./scripts/claims.py prune
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import claims as claims_lib
from lib import issue_lock
from lib.prd_store import load_prd


def _release_labels(bot_dir, story_ids):
    """Take the in-progress label off the issues of the given stories."""
    if not story_ids or not issue_lock.label_name(bot_dir=bot_dir):
        return
    try:
        stories = load_prd(os.path.join(bot_dir, "data", "prd.json")).get("stories", [])
    except (OSError, ValueError) as e:
        print(
            f"WARNING: could not read the PRD to release labels: {e}", file=sys.stderr
        )
        return
    for story in stories:
        if story.get("id") in story_ids:
            issue = issue_lock.issue_number(story)
            if issue:
                issue_lock.release(issue, bot_dir=bot_dir)


def main():
    bot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(description="Inspect and clear story claims")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="Show claims held by live runs")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("release", help="Clear claims by story, by slot, or both")
    p.add_argument("--story")
    p.add_argument("--slot", type=int)

    sub.add_parser("prune", help="Clear claims whose run is gone")

    args = parser.parse_args()

    if args.command == "list":
        active = claims_lib.active(bot_dir)
        if args.json:
            print(json.dumps(active, indent=2))
        elif not active:
            print("No stories are claimed.")
        else:
            for sid, c in sorted(active.items()):
                print(
                    f"{sid}: slot {c.get('slot')} (pid {c.get('pid')}, "
                    f"since {c.get('claimedAt')})"
                )
        return 0

    if args.command == "release":
        if args.story is None and args.slot is None:
            print("Error: release needs --story, --slot, or both", file=sys.stderr)
            return 1
        dropped = claims_lib.release(bot_dir, story_id=args.story, slot=args.slot)
        _release_labels(bot_dir, set(dropped))
        print(f"Released: {', '.join(sorted(set(dropped))) or 'nothing'}")
        return 0

    dropped = claims_lib.release(bot_dir)
    _release_labels(bot_dir, set(dropped))
    print(f"Pruned: {', '.join(sorted(set(dropped))) or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
