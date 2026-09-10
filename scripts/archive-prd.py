#!/usr/bin/env python3
"""Archive merged and invalid stories from prd.json to prd.archived.json.

Keeps only active work in the main PRD. Writes cleaned prd.json in-place
(via temp file) and outputs a full recap to stdout.

Runs under the PRD lock, and leaves alone any story a live run has claimed:
removing a story mid-iteration would make that run's next status update fail
with "story not found".
"""

import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import claims as claims_lib
from lib.prd_store import bot_dir_for, prd_lock


def main():
    if len(sys.argv) < 2:
        print("Usage: clean-prd.py path/to/prd.json", file=sys.stderr)
        sys.exit(1)

    prd_path = sys.argv[1]
    prd_dir = os.path.dirname(prd_path) or "."
    archive_path = os.path.join(prd_dir, "prd.archived.json")

    with prd_lock(prd_path):
        _archive(prd_path, prd_dir, archive_path)


def _archive(prd_path, prd_dir, archive_path):
    # Read existing PRD
    try:
        with open(prd_path, "r") as f:
            prd = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: PRD file not found at {prd_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {prd_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Read existing archive if it exists
    old_stories = []
    if os.path.exists(archive_path):
        try:
            with open(archive_path, "r") as f:
                old_prd = json.load(f)
                old_stories = old_prd.get("stories", [])
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"WARNING: Could not read {archive_path}: {e}", file=sys.stderr)
            print("Starting with empty archive.", file=sys.stderr)

    # Split stories into active and to-archive
    active_stories = []
    archived_stories = []

    claimed = set(claims_lib.active(bot_dir_for(prd_path), locked=True))
    held_back = []
    for story in prd.get("stories", []):
        if story.get("status") in ("merged", "invalid"):
            if story.get("id") in claimed:
                held_back.append(story.get("id"))
                active_stories.append(story)
            else:
                archived_stories.append(story)
        else:
            active_stories.append(story)

    if held_back:
        print(
            f"Leaving {', '.join(held_back)} in place — a run is working "
            f"on them right now.",
            file=sys.stderr,
        )

    if not archived_stories:
        print("No merged or invalid stories to archive. Nothing to do.")
        sys.exit(0)

    # Merge with existing archived stories (dedup by ID, latest wins)
    archive_map = {s["id"]: s for s in old_stories}
    for story in archived_stories:
        archive_map[story["id"]] = story

    all_archived = sorted(archive_map.values(), key=lambda s: s["id"])

    # Build output structures
    new_prd = copy.deepcopy(prd)
    new_prd["stories"] = active_stories

    archive_data = {
        "stories": all_archived,
    }

    # Write archive file
    try:
        with open(archive_path, "w") as f:
            json.dump(archive_data, f, indent=2)
            f.write("\n")
    except IOError as e:
        print(f"ERROR: Could not write {archive_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Write cleaned PRD atomically via temp file
    try:
        fd, tmp_path = tempfile.mkstemp(dir=prd_dir, suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(new_prd, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, prd_path)
    except IOError as e:
        print(f"ERROR: Could not write {prd_path}: {e}", file=sys.stderr)
        # Clean up temp file if it exists
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        sys.exit(1)

    # Print recap
    merged = [s for s in archived_stories if s.get("status") == "merged"]
    invalid = [s for s in archived_stories if s.get("status") == "invalid"]

    print("# PRD Cleaning Recap\n")
    print("## Summary")
    print(
        f"Archived {len(archived_stories)} completed stories from "
        f"prd.json to prd.archived.json.\n"
    )

    print(f"## Archived Stories ({len(archived_stories)} total)\n")

    if merged:
        print(f"### Merged ({len(merged)} stories)")
        for s in merged:
            print(f"- {s['id']}: {s.get('title', 'No title')}")
        print()

    if invalid:
        print(f"### Invalid ({len(invalid)} stories)")
        for s in invalid:
            print(f"- {s['id']}: {s.get('title', 'No title')}")
        print()

    # Count active stories by status
    status_counts = {}
    for s in active_stories:
        st = s.get("status", "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1

    print(f"## Active Stories Remaining ({len(active_stories)} total)\n")
    for st in ("pending", "committed", "pushed", "skipped"):
        count = status_counts.get(st, 0)
        if count:
            print(f"- {st.capitalize()}: {count}")

    print("\n## Statistics")
    print(
        f"- Stories archived this run: {len(archived_stories)} "
        f"({len(merged)} merged, {len(invalid)} invalid)"
    )
    print(f"- Total stories in prd.archived.json: {len(all_archived)}")
    print(f"- Active stories in prd.json: {len(active_stories)}")


if __name__ == "__main__":
    main()
