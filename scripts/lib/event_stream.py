#!/usr/bin/env python3
"""Event Stream & Telemetry for brave-dev-loop.

Provides structured, append-only event sourcing for all loop iterations.
Events are written atomically to ~/.brave-dev-loop/sessions/<session_id>/events.jsonl
and can be streamed in real-time by WebSockets, SSE, TUIs, or the CLI.
Also handles Human-in-the-Loop (HITL) steering commands and pause/resume states.
"""

import glob
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

# Constants
DEFAULT_LOOP_HOME = os.path.expanduser("~/.brave-dev-loop")


class EventType:
    SESSION_START = "SESSION_START"
    SESSION_END = "SESSION_END"
    ITERATION_START = "ITERATION_START"
    ITERATION_END = "ITERATION_END"
    PHASE_CHANGE = "PHASE_CHANGE"
    TOOL_CALL = "TOOL_CALL"
    FILE_DIFF = "FILE_DIFF"
    GATE_STATUS = "GATE_STATUS"
    TOKEN_USAGE = "TOKEN_USAGE"
    STEER_HINT = "STEER_HINT"
    CONTROL_COMMAND = "CONTROL_COMMAND"
    LOG_MESSAGE = "LOG_MESSAGE"


@dataclass
class LoopEvent:
    event_type: str
    story_id: str = ""
    slot: int = 1
    session_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoopEvent":
        return cls(
            event_type=data.get("event_type", EventType.LOG_MESSAGE),
            story_id=data.get("story_id", ""),
            slot=int(data.get("slot", 1)),
            session_id=data.get("session_id", ""),
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )

    @classmethod
    def from_json(cls, line: str) -> Optional["LoopEvent"]:
        line = line.strip()
        if not line:
            return None
        try:
            return cls.from_dict(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            return None


def get_sessions_dir(loop_home: Optional[str] = None) -> str:
    home = loop_home or os.environ.get("BRAVE_DEV_LOOP_HOME", DEFAULT_LOOP_HOME)
    sessions_dir = os.path.join(home, "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    return sessions_dir


def get_events_file_path(session_dir: str) -> str:
    return os.path.join(session_dir, "events.jsonl")


def get_control_file_path(session_dir: str) -> str:
    return os.path.join(session_dir, "control.json")


def emit_event(session_dir: str, event: LoopEvent) -> bool:
    """Appends an event to the session's events.jsonl file safely."""
    try:
        os.makedirs(session_dir, exist_ok=True)
        events_file = get_events_file_path(session_dir)
        line = event.to_json() + "\n"
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
        return True
    except OSError:
        return False


def read_events(session_dir: str, limit: Optional[int] = None) -> List[LoopEvent]:
    """Reads all or recent events from a session's events.jsonl file."""
    events_file = get_events_file_path(session_dir)
    if not os.path.exists(events_file):
        return []

    events = []
    try:
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                ev = LoopEvent.from_json(line)
                if ev:
                    events.append(ev)
    except OSError:
        return []

    if limit is not None and limit > 0:
        return events[-limit:]
    return events


def tail_events(
    session_dir: str, follow: bool = True, poll_interval: float = 0.5
) -> Generator[LoopEvent, None, None]:
    """Yields events from events.jsonl as they arrive, similar to tail -f."""
    events_file = get_events_file_path(session_dir)
    last_pos = 0

    while True:
        if os.path.exists(events_file):
            try:
                with open(events_file, "r", encoding="utf-8") as f:
                    f.seek(last_pos)
                    for line in f:
                        ev = LoopEvent.from_json(line)
                        if ev:
                            yield ev
                    last_pos = f.tell()
            except OSError:
                pass

        if not follow:
            break
        time.sleep(poll_interval)


# --- Control / Steering State Management ---


def get_control_state(session_dir: str) -> Dict[str, Any]:
    """Reads the current control/steering state for a session."""
    control_file = get_control_file_path(session_dir)
    if not os.path.exists(control_file):
        return {
            "paused": False,
            "steer_hints": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    try:
        with open(control_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {
            "paused": False,
            "steer_hints": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }


def save_control_state(session_dir: str, state: Dict[str, Any]) -> bool:
    """Saves the control/steering state atomically."""
    os.makedirs(session_dir, exist_ok=True)
    control_file = get_control_file_path(session_dir)
    tmp_file = control_file + f".tmp.{os.getpid()}"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
        os.replace(tmp_file, control_file)
        return True
    except OSError:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass
        return False


def submit_steer_hint(session_dir: str, hint: str, author: str = "operator") -> bool:
    """Appends a human-in-the-loop steering hint to the session control queue."""
    state = get_control_state(session_dir)
    hints = state.get("steer_hints", [])
    hint_obj = {
        "hint": hint.strip(),
        "author": author,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "consumed": False,
    }
    hints.append(hint_obj)
    state["steer_hints"] = hints
    saved = save_control_state(session_dir, state)
    if saved:
        emit_event(
            session_dir,
            LoopEvent(
                event_type=EventType.STEER_HINT,
                payload=hint_obj,
            ),
        )
    return saved


def pop_pending_steer_hints(session_dir: str) -> List[str]:
    """Fetches unconsumed steering hints and marks them as consumed."""
    state = get_control_state(session_dir)
    hints = state.get("steer_hints", [])
    pending = []
    for h in hints:
        if not h.get("consumed", False):
            pending.append(h.get("hint", ""))
            h["consumed"] = True

    if pending:
        save_control_state(session_dir, state)
    return pending


def set_paused_state(session_dir: str, paused: bool) -> bool:
    """Pauses or resumes the session loop."""
    state = get_control_state(session_dir)
    state["paused"] = paused
    saved = save_control_state(session_dir, state)
    if saved:
        emit_event(
            session_dir,
            LoopEvent(
                event_type=EventType.CONTROL_COMMAND,
                payload={"command": "PAUSE" if paused else "RESUME"},
            ),
        )
    return saved


def is_session_paused(session_dir: str) -> bool:
    state = get_control_state(session_dir)
    return bool(state.get("paused", False))


# --- Session Discovery Helpers ---


def find_all_sessions(loop_home: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns metadata for all sessions on the host, newest first."""
    sessions_dir = get_sessions_dir(loop_home)
    result = []
    pattern = os.path.join(sessions_dir, "*")
    for path in sorted(glob.glob(pattern), reverse=True):
        if os.path.isdir(path):
            session_id = os.path.basename(path)
            events_file = get_events_file_path(path)
            has_events = os.path.exists(events_file)
            mtime = os.path.getmtime(path)
            result.append(
                {
                    "session_id": session_id,
                    "session_dir": path,
                    "has_events": has_events,
                    "modified_at": datetime.fromtimestamp(
                        mtime, timezone.utc
                    ).isoformat(),
                }
            )
    return result


def find_active_sessions(bot_dir: str) -> List[Dict[str, Any]]:
    """Inspects run slot locks and metadata to discover running sessions."""
    active = []
    runs_dir = os.path.join(bot_dir, "data", "runs")
    if not os.path.exists(runs_dir):
        return active

    for meta_file in glob.glob(os.path.join(runs_dir, "slot-*.json")):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            slot = data.get("slot")
            lockfile = (
                os.path.join(bot_dir, ".run.lock")
                if slot == 1
                else os.path.join(bot_dir, f".run.slot-{slot}.lock")
            )

            # Check if lockfile exists and has an owner pid
            pid = data.get("pid")
            is_running = False
            if pid:
                try:
                    os.kill(int(pid), 0)
                    is_running = True
                except (OSError, ValueError):
                    is_running = False

            if is_running:
                data["lockfile"] = lockfile
                active.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return active


# --- Stream Parsing for Agent Output ---


class StreamParser:
    """Parses agent execution streams (Claude stream-json, Codex JSON, raw logs)

    and emits structured LoopEvents.
    """

    def __init__(self, session_dir: str, slot: int = 1, story_id: str = ""):
        self.session_dir = session_dir
        self.slot = slot
        self.story_id = story_id

    def parse_line(self, raw_line: str) -> Optional[LoopEvent]:
        line = raw_line.strip()
        if not line:
            return None

        # Try to parse as JSON (Claude stream-json or Codex json format)
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                # Claude stream-json tool use format
                if data.get("type") == "tool_use" or "tool_name" in data:
                    name = data.get("name") or data.get("tool_name", "unknown")
                    inp = data.get("input") or data.get("parameters", {})
                    ev = LoopEvent(
                        event_type=EventType.TOOL_CALL,
                        slot=self.slot,
                        story_id=self.story_id,
                        payload={"tool": name, "input": inp},
                    )
                    emit_event(self.session_dir, ev)
                    return ev

                # Token usage report
                if "usage" in data or "total_tokens" in data:
                    usage = data.get("usage") or data
                    ev = LoopEvent(
                        event_type=EventType.TOKEN_USAGE,
                        slot=self.slot,
                        story_id=self.story_id,
                        payload=usage,
                    )
                    emit_event(self.session_dir, ev)
                    return ev
            except (json.JSONDecodeError, ValueError):
                pass

        # Check for Git diff lines
        if line.startswith("diff --git "):
            ev = LoopEvent(
                event_type=EventType.FILE_DIFF,
                slot=self.slot,
                story_id=self.story_id,
                payload={"diff_header": line},
            )
            emit_event(self.session_dir, ev)
            return ev

        # Check for Gate status lines
        if "[✓]" in line or "[X]" in line or "PASS" in line or "FAIL" in line:
            ev = LoopEvent(
                event_type=EventType.GATE_STATUS,
                slot=self.slot,
                story_id=self.story_id,
                payload={"line": line},
            )
            emit_event(self.session_dir, ev)
            return ev

        return None


# --- CLI Interface ---


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Event Stream CLI for brave-dev-loop")
    subparsers = parser.add_subparsers(dest="cmd")

    emit_parser = subparsers.add_parser("emit", help="Emit a custom event")
    emit_parser.add_argument("--session-dir", required=True)
    emit_parser.add_argument("--type", required=True)
    emit_parser.add_argument("--story-id", default="")
    emit_parser.add_argument("--slot", type=int, default=1)
    emit_parser.add_argument("--payload", default="{}")

    tail_parser = subparsers.add_parser("tail", help="Tail events in real-time")
    tail_parser.add_argument("--session-dir", required=True)
    tail_parser.add_argument("--follow", action="store_true")

    steer_parser = subparsers.add_parser("steer", help="Submit a steering hint")
    steer_parser.add_argument("--session-dir", required=True)
    steer_parser.add_argument("--hint", required=True)
    steer_parser.add_argument("--author", default="operator")

    pop_parser = subparsers.add_parser(
        "pop-steer", help="Pop unconsumed steering hints"
    )
    pop_parser.add_argument("--session-dir", required=True)

    pause_parser = subparsers.add_parser("pause", help="Pause or resume session")
    pause_parser.add_argument("--session-dir", required=True)
    pause_parser.add_argument(
        "--resume", action="store_true", help="Resume instead of pause"
    )

    is_paused_parser = subparsers.add_parser(
        "is-paused", help="Exit 0 if paused, 1 if not"
    )
    is_paused_parser.add_argument("--session-dir", required=True)

    args = parser.parse_args()

    if args.cmd == "emit":
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError:
            payload = {"raw": args.payload}
        ev = LoopEvent(
            event_type=args.type,
            story_id=args.story_id,
            slot=args.slot,
            payload=payload,
        )
        success = emit_event(args.session_dir, ev)
        sys.exit(0 if success else 1)

    elif args.cmd == "tail":
        for ev in tail_events(args.session_dir, follow=args.follow):
            print(ev.to_json())

    elif args.cmd == "steer":
        success = submit_steer_hint(args.session_dir, args.hint, args.author)
        sys.exit(0 if success else 1)

    elif args.cmd == "pop-steer":
        hints = pop_pending_steer_hints(args.session_dir)
        print(json.dumps(hints))

    elif args.cmd == "pause":
        success = set_paused_state(args.session_dir, paused=not args.resume)
        sys.exit(0 if success else 1)

    elif args.cmd == "is-paused":
        sys.exit(0 if is_session_paused(args.session_dir) else 1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
