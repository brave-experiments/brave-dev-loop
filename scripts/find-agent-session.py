#!/usr/bin/env python3
"""Find the session an agent CLI just wrote, so a later run can read it back.

Prints one JSON object on stdout:

  {"agent", "sessionId", "directory", "transcript", "auditLog", "resumeCommand"}

Absent pieces are empty strings. Exit 1 when no session can be identified, 2 for
an agent whose session store this does not know how to read.

Only Claude is told its session id up front (`claude --session-id`), so for
every other agent the session is identified after the fact: newest record for
the directory the agent ran in, no older than --since.

Usage:
  find-agent-session.py --agent bravebot --cwd /path/to/dir --since 1789000000
  find-agent-session.py --agent claude --cwd /path/to/dir --session-id <uuid>
"""

import argparse
import json
import os
import re
import sys


def slugify(path):
    """Both Claude and bravebot name a session directory after the working
    directory, with every non-alphanumeric run replaced by a single dash."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", path)


def same_dir(a, b):
    if not a or not b:
        return False
    return os.path.realpath(a) == os.path.realpath(b)


def find_bravebot(home, cwd, since, exclude):
    """~/.bravebot/sessions/<slug>/<id>.json, with a sibling <id>.audit.jsonl.

    The slug is derived from the working directory, but the record carries its
    own `directory` field — match on that instead, so a change to bravebot's
    naming does not silently return another directory's session.
    """
    root = os.path.join(home, ".bravebot", "sessions")
    best = None
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as f:
                    record = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            session_id = str(record.get("id") or "")
            if not session_id or session_id == exclude:
                continue
            if not same_dir(record.get("directory"), cwd):
                continue
            try:
                updated = int(record.get("updated") or record.get("started") or 0)
            except (TypeError, ValueError):
                updated = 0
            if since and updated < since:
                continue
            if best is None or updated > best[0]:
                best = (updated, session_id, path)

    if best is None:
        return None

    _updated, session_id, path = best
    audit = path[: -len(".json")] + ".audit.jsonl"
    return {
        "sessionId": session_id,
        "transcript": path,
        "auditLog": audit if os.path.exists(audit) else "",
        "resumeCommand": f"bravebot --resume {session_id}",
    }


def find_claude(home, cwd, session_id):
    """~/.claude/projects/<slug>/<id>.jsonl. The id is always known already."""
    if not session_id:
        return None
    transcript = os.path.join(
        home,
        ".claude",
        "projects",
        slugify(os.path.abspath(cwd)),
        f"{session_id}.jsonl",
    )
    return {
        "sessionId": session_id,
        "transcript": transcript if os.path.exists(transcript) else "",
        "auditLog": "",
        "resumeCommand": f"claude --resume {session_id}",
    }


def find_codex(home, cwd, since, exclude):
    """~/.codex/sessions/<y>/<m>/<d>/rollout-*.jsonl. Best effort: the cwd lives
    inside the first line of the rollout, so match on the file's text."""
    root = os.path.join(home, ".codex", "sessions")
    target = os.path.realpath(cwd)
    best = None
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if since and stat.st_mtime < since:
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    head = f.readline()
            except OSError:
                continue
            if target not in head:
                continue
            session_id = os.path.splitext(name)[0]
            if session_id == exclude:
                continue
            if best is None or stat.st_mtime > best[0]:
                best = (stat.st_mtime, session_id, path)

    if best is None:
        return None

    _mtime, session_id, path = best
    return {
        "sessionId": session_id,
        "transcript": path,
        "auditLog": "",
        "resumeCommand": "codex resume --last",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--cwd", required=True, help="Directory the agent ran in")
    parser.add_argument(
        "--since",
        type=int,
        default=0,
        help="Ignore sessions older than this epoch time",
    )
    parser.add_argument(
        "--exclude", default="", help="Session id to skip (e.g. the base run's)"
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Known session id (Claude, which is told its id)",
    )
    parser.add_argument("--home", default=os.path.expanduser("~"))
    args = parser.parse_args()

    if args.agent == "bravebot":
        found = find_bravebot(args.home, args.cwd, args.since, args.exclude)
    elif args.agent == "claude":
        found = find_claude(args.home, args.cwd, args.session_id)
    elif args.agent == "codex":
        found = find_codex(args.home, args.cwd, args.since, args.exclude)
    else:
        # cursor-agent keeps no documented session store to read.
        print(
            f"Cannot locate a session for agent '{args.agent}'; "
            "read the iteration log instead.",
            file=sys.stderr,
        )
        return 2

    if not found:
        print(
            f"No {args.agent} session found for {args.cwd}"
            + (f" after {args.since}" if args.since else ""),
            file=sys.stderr,
        )
        return 1

    found["agent"] = args.agent
    found["directory"] = os.path.abspath(args.cwd)
    print(json.dumps(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
