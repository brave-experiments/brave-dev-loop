#!/usr/bin/env python3
"""Update the review-prs cache with a PR's HEAD SHA after review.

Also updates _last_run timestamp so the next fetch-prs.py run uses it
as the cutoff instead of a fixed N-day window. This prevents gaps if a
cron run is missed.

Usage:
  update-cache.py <pr_number> <head_ref_oid>            # Update SHA only
  update-cache.py <pr_number> <head_ref_oid> --approve   # Update SHA + mark approved
"""

import os
import sys
from datetime import datetime, timezone

_script_dir = os.path.dirname(os.path.abspath(__file__))
_bot_dir = os.path.join(_script_dir, "..", "..", "..")
sys.path.insert(0, os.path.join(_bot_dir, "scripts"))
from lib.file_lock import locked_json_update
from lib.load_config import load_config, require_config

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = [a for a in sys.argv[1:] if a.startswith("--")]

if len(args) != 2:
    print(
        f"Usage: {sys.argv[0]} <pr_number> <head_ref_oid> [--approve]", file=sys.stderr
    )
    sys.exit(1)

pr_number = args[0]
head_ref_oid = args[1]
approve = "--approve" in flags

# Absolute, not relative to the caller's cwd: the cache is one file per bot
# directory, and two runs that disagreed about where it lives would each keep
# half the entries.
cache_path = os.path.normpath(
    os.path.join(_bot_dir, ".ignore", "review-prs-cache.json")
)

# Read, mutate and write under one lock. Review sessions overlap — the
# review-request poll runs every five minutes and does not wait for the
# previous one — so two of them finish moments apart, and an unlocked
# read-modify-write drops whichever entry was written first. The write is
# atomic for the same reason: a torn file parses as no cache at all, which
# sends the next fetch-prs.py back to its fixed N-day window.
with locked_json_update(cache_path) as cache:
    cache[pr_number] = head_ref_oid
    cache["_last_run"] = datetime.now(timezone.utc).isoformat()

    if approve:
        approved = set(cache.get("_approved", []))
        approved.add(pr_number)
        cache["_approved"] = sorted(approved)

_config = load_config()
_pr_repo = require_config(_config, "project.prRepository")

status = "approved + cached" if approve else "cached"
print(
    f"Cache updated ({status}): [PR #{pr_number}](https://github.com/{_pr_repo}/pull/{pr_number}) -> {head_ref_oid}"
)
