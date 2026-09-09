#!/usr/bin/env python3
"""Repair path keys in an existing config.json.

targetRepoPath and bestPractices.docsDir were historically resolved against
two different bases -- the bot dir in some consumers, its parent in others --
so deployments exist with a docsDir that resolves nowhere. This re-points such
a docsDir at the directory the target repo actually lives in and stores the
canonical bot-dir-relative form.

Runs on every `make setup`, wizard or not. A no-op when the path already
resolves, so it is safe to re-run.

Usage:
  scripts/repair-config-paths.py [--config PATH] [--bot-root DIR] [--dry-run]

Exit codes:
  0 - success (repaired, or nothing to do)
  2 - error
"""

import argparse
import json
import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_default_bot_root = os.path.dirname(_script_dir)


def resolve(path, bot_root, require_git):
    """Resolve `path` against the bot dir, then its parent. None if neither hits."""
    if not path:
        return None
    if os.path.isabs(path):
        return os.path.normpath(path)
    bases = (
        os.path.normpath(os.path.join(bot_root, path)),
        os.path.normpath(os.path.join(os.path.dirname(bot_root), path)),
    )
    for candidate in bases:
        probe = os.path.join(candidate, ".git") if require_git else candidate
        if os.path.exists(probe):
            return candidate
    return None


def repair(config, bot_root):
    """Return (fixed_docs_dir, old_value) or (None, reason) if nothing changed."""
    target_repo = (config.get("project") or {}).get("targetRepoPath") or ""
    bp = config.setdefault("bestPractices", {})
    current = bp.get("docsDir") or ""

    if resolve(current, bot_root, False) is not None:
        return None, "already resolves"

    target_abs = resolve(target_repo, bot_root, True)
    if target_abs is None:
        return None, "target repo not found"

    fixed = os.path.relpath(os.path.join(target_abs, "docs"), bot_root)
    bp["docsDir"] = fixed
    return fixed, current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default=os.path.join(_default_bot_root, "config.json")
    )
    parser.add_argument("--bot-root", default=_default_bot_root)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        return 0

    try:
        with open(args.config) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  Could not read {args.config}: {e}", file=sys.stderr)
        return 2

    fixed, detail = repair(config, args.bot_root)
    if fixed is None:
        if detail == "target repo not found":
            print(
                "  docsDir looks wrong but the target repo could not be located "
                "— leaving it alone."
            )
        return 0

    print(f"  Repaired bestPractices.docsDir: {detail!r} -> {fixed!r}")
    if args.dry_run:
        return 0

    with open(args.config, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
