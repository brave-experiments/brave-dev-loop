#!/usr/bin/env python3
"""Fetch the current nightly version from the Brave Release Schedule wiki.

Prints the nightly version (e.g. '1.89.x') to stdout.
Falls back to empty output on failure (caller should handle gracefully).
"""

import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.load_config import load_config, require_config

_config = load_config()
_issue_repo = require_config(_config, "project.issueRepository")

WIKI_URL = (
    f"https://raw.githubusercontent.com/wiki/{_issue_repo}/Brave-Release-Schedule.md"
)


def main():
    try:
        with urllib.request.urlopen(WIKI_URL, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"Failed to fetch release schedule: {e}", file=sys.stderr)
        return

    channel_cols = None
    for line in content.split("\n"):
        cells = [c.strip().strip("*").strip() for c in line.split("|") if c.strip()]
        if not cells:
            continue

        cells_lower = [c.lower() for c in cells]
        if "channel" in cells_lower[0] and "nightly" in cells_lower:
            channel_cols = cells_lower[1:]
            continue

        if channel_cols and "milestone" in cells[0].lower():
            ver_cells = cells[1:]
            try:
                idx = channel_cols.index("nightly")
            except ValueError:
                break
            if idx < len(ver_cells):
                m = re.search(r"(\d+\.\d+\.x)", ver_cells[idx])
                if m:
                    print(m.group(1))
                    return
            break

    print("Could not parse nightly version from wiki", file=sys.stderr)


if __name__ == "__main__":
    main()
