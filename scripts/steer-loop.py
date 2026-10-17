#!/usr/bin/env python3
"""Steer Loop CLI: Human-in-the-Loop Steering for brave-dev-loop.

Usage:
    ./scripts/steer-loop.py --list
    ./scripts/steer-loop.py --slot 1 "Focus on fixing shields_service.cc only"
    ./scripts/steer-loop.py --slot 1 --pause
    ./scripts/steer-loop.py --slot 1 --resume
    ./scripts/steer-loop.py --slot 1 --status
"""

import argparse
import json
import os
import sys

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BOT_DIR, "scripts"))

from lib import event_stream
from lib.event_stream import (
    find_all_sessions,
    get_control_state,
    is_session_paused,
    set_paused_state,
    submit_steer_hint,
)


def resolve_session_dir(slot: int = 1, session_id: str = "") -> tuple[str, str]:
    """Finds the session directory for a slot or session ID."""
    if session_id:
        sessions_dir = event_stream.get_sessions_dir()
        target = os.path.join(sessions_dir, session_id)
        if os.path.isdir(target):
            return target, session_id
        # Check if full path passed
        if os.path.isdir(session_id):
            return session_id, os.path.basename(session_id)
        print(f"Error: Session '{session_id}' not found.", file=sys.stderr)
        sys.exit(1)

    # Find active slot meta file in data/runs/slot-<slot>.json
    meta_path = os.path.join(BOT_DIR, "data", "runs", f"slot-{slot}.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            # Find recent session directory matching slot
            s_dir = meta.get("sessionDir")
            if s_dir and os.path.isdir(s_dir):
                return s_dir, os.path.basename(s_dir)
        except (json.JSONDecodeError, OSError):
            pass

    # Look up in ~/.brave-dev-loop/sessions for newest session matching slot
    sessions = find_all_sessions()
    slot_suffix = f"-slot-{slot}"
    for s in sessions:
        if s["session_id"].endswith(slot_suffix):
            return s["session_dir"], s["session_id"]

    # Fallback to newest session
    if sessions:
        latest = sessions[0]
        return latest["session_dir"], latest["session_id"]

    print("Error: No active or recent session found.", file=sys.stderr)
    sys.exit(1)


def list_sessions():
    sessions = find_all_sessions()
    print(f"{'SESSION ID':<36} {'STATUS':<10} {'MODIFIED':<24} {'DIR'}")
    print("-" * 90)
    for s in sessions[:15]:
        sid = s["session_id"]
        sdir = s["session_dir"]
        paused = is_session_paused(sdir)
        status = "PAUSED" if paused else "ACTIVE"
        mod = s["modified_at"]
        print(f"{sid:<36} {status:<10} {mod:<24} {sdir}")


def main():
    parser = argparse.ArgumentParser(
        description="Human-in-the-Loop Steering for brave-dev-loop"
    )
    parser.add_argument("--list", action="store_true", help="List all sessions")
    parser.add_argument(
        "--slot",
        type=int,
        default=1,
        help="Target slot number (default: 1)",
    )
    parser.add_argument("--session", default="", help="Target session ID")
    parser.add_argument("--pause", action="store_true", help="Pause the target loop")
    parser.add_argument("--resume", action="store_true", help="Resume the target loop")
    parser.add_argument(
        "--status", action="store_true", help="Show loop control status"
    )
    parser.add_argument(
        "instruction",
        nargs="*",
        help="Steering instruction to inject into the agent's prompt",
    )

    args = parser.parse_args()

    if args.list:
        list_sessions()
        return

    session_dir, session_id = resolve_session_dir(args.slot, args.session)

    if args.pause:
        set_paused_state(session_dir, paused=True)
        print(f"✓ Paused session: {session_id}")
        return

    if args.resume:
        set_paused_state(session_dir, paused=False)
        print(f"✓ Resumed session: {session_id}")
        return

    if args.status:
        state = get_control_state(session_dir)
        print(f"Session: {session_id}")
        print(f"Directory: {session_dir}")
        print(f"Paused: {state.get('paused', False)}")
        hints = state.get("steer_hints", [])
        print(f"Steer Hints ({len(hints)} total):")
        for idx, h in enumerate(hints, 1):
            cons = "[CONSUMED]" if h.get("consumed") else "[PENDING]"
            print(f"  {idx}. {cons} ({h.get('timestamp')}): {h.get('hint')}")
        return

    hint_text = " ".join(args.instruction).strip()
    if hint_text:
        submit_steer_hint(session_dir, hint_text)
        print(f"✓ Queued steering hint for session {session_id}:")
        print(f'  "{hint_text}"')
        print("  (Will be injected into the next prompt / turn)")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
