#!/usr/bin/env python3
"""Release in-progress labels that a run left behind on its way out.

A run labels the issue it is working so a run on another machine filters that
story out (scripts/lib/issue_lock.py). Nothing refreshes the label and a
machine that dies never removes it, so run.sh calls this at start: a label
older than --max-age-hours means no run has that issue any more, whichever
machine put it there.

Only issues assigned to the bot are ever touched. Python and gh only; no agent
is started, so this costs no tokens.

Usage:
  scripts/sweep-in-progress.py                    # release anything over 6h
  scripts/sweep-in-progress.py --max-age-hours 2  # a shorter leash
  scripts/sweep-in-progress.py --dry-run          # report only, change nothing

Exit codes:
  0 - swept (labels released, or none were old enough)
  2 - error
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import issue_lock
from lib.load_config import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=issue_lock.DEFAULT_MAX_AGE_HOURS,
        help=f"Release labels older than this (default: "
        f"{issue_lock.DEFAULT_MAX_AGE_HOURS})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be released"
    )
    args = parser.parse_args()

    bot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = load_config()
    label = issue_lock.label_name(config, bot_dir)
    if not label:
        print("No in-progress label configured for this project; nothing to sweep.")
        return 0

    released = issue_lock.sweep(
        args.max_age_hours, config, bot_dir, dry_run=args.dry_run
    )
    if not released:
        print(f"No {label} label is older than {args.max_age_hours}h.")
        return 0

    verb = "Would release" if args.dry_run else "Released"
    listed = ", ".join(f"#{n}" for n in released)
    print(f"{verb} {label} on {len(released)} issue(s): {listed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
