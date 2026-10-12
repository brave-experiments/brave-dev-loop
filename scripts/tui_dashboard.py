#!/usr/bin/env python3
"""Terminal UI (TUI) Dashboard for brave-dev-loop.

Provides a live, zero-dependency terminal dashboard displaying:
- Loop state & active run slot
- Current story metadata & lifecycle status
- Live stream of recent events & tool calls
- Interactive keyboard shortcuts (q: quit, p: pause, r: resume, s: steer)

Usage:
    ./scripts/tui_dashboard.py [--slot 1]
"""

import argparse
import curses
import json
import os
import sys

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BOT_DIR, "scripts"))

from lib.event_stream import (
    find_all_sessions,
    is_session_paused,
    read_events,
    set_paused_state,
    submit_steer_hint,
)


def safe_addstr(stdscr, y: int, x: int, text: str, attr: int = 0):
    """Safely writes a string avoiding curses border overflow errors."""
    try:
        max_y, max_x = stdscr.getmaxyx()
        if 0 <= y < max_y and 0 <= x < max_x:
            avail = max_x - x - 1
            if avail > 0:
                stdscr.addstr(y, x, text[:avail], attr)
    except curses.error:
        pass


def draw_dashboard(stdscr, slot: int = 1):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(1000)  # 1 second refresh rate

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)  # Accent / Story ID
    curses.init_pair(2, curses.COLOR_GREEN, -1)  # Success / Live / Gate pass
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # Warning / Paused
    curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # Phase change
    curses.init_pair(5, curses.COLOR_RED, -1)  # Danger / Steer hint

    prompt_mode = False
    input_buffer = ""

    while True:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.erase()

        # Find active session
        sessions = find_all_sessions()
        session_dir = ""
        session_id = "No active session"
        if sessions:
            session_dir = sessions[0]["session_dir"]
            session_id = sessions[0]["session_id"]

        paused = is_session_paused(session_dir) if session_dir else False
        state_str = "PAUSED" if paused else "RUNNING"
        state_color = (
            curses.color_pair(3) if paused else curses.color_pair(2)
        ) | curses.A_BOLD

        # Header bar
        header_text = f" ⚡ brave-dev-loop Live Telemetry (Slot {slot}) "
        safe_addstr(stdscr, 0, 0, header_text, curses.A_REVERSE)

        status_text = f" [{state_str}] "
        if len(header_text) + len(status_text) < max_x:
            safe_addstr(
                stdscr,
                0,
                max_x - len(status_text) - 1,
                status_text,
                state_color,
            )

        # Active Story Info
        current_story = "-"
        story_status = "-"
        meta_file = os.path.join(BOT_DIR, "data", "runs", f"slot-{slot}.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                current_story = meta.get("storyId", "-")
            except (json.JSONDecodeError, OSError):
                pass

        prd_file = os.path.join(BOT_DIR, "data", "prd.json")
        if os.path.exists(prd_file) and current_story != "-":
            try:
                with open(prd_file, "r", encoding="utf-8") as f:
                    prd = json.load(f)
                for s in prd.get("stories", []):
                    if s.get("id") == current_story:
                        story_status = s.get("status", "-")
                        break
            except (json.JSONDecodeError, OSError):
                pass

        story_line = (
            f" Story: {current_story} | Status: {story_status} | Session: {session_id}"
        )
        safe_addstr(stdscr, 2, 0, story_line, curses.color_pair(1))
        safe_addstr(stdscr, 3, 0, "─" * (max_x - 1))

        # Event stream pane
        events = read_events(session_dir, limit=max_y - 8) if session_dir else []
        row = 4
        if not events:
            safe_addstr(stdscr, row, 2, "(Waiting for loop events...)", curses.A_DIM)
        else:
            for ev in events:
                if row >= max_y - 3:
                    break
                ts = ev.timestamp.split("T")[-1][:8] if ev.timestamp else ""
                etype = ev.event_type
                color = curses.color_pair(1)
                if etype == "TOOL_CALL":
                    color = curses.color_pair(1)
                elif etype == "PHASE_CHANGE":
                    color = curses.color_pair(4)
                elif etype == "GATE_STATUS":
                    color = curses.color_pair(2)
                elif etype == "STEER_HINT":
                    color = curses.color_pair(5)

                payload = ev.payload
                summary = ""
                if isinstance(payload, dict):
                    if "tool" in payload:
                        summary = f"tool: {payload.get('tool')}"
                    elif "hint" in payload:
                        summary = f"hint: {payload.get('hint')}"
                    elif "line" in payload:
                        summary = str(payload.get("line"))[:60]
                    else:
                        summary = str(payload)[:60]
                else:
                    summary = str(payload)[:60]

                line_str = f" [{ts}] {etype:<14} {summary}"
                safe_addstr(stdscr, row, 0, line_str, color)
                row += 1

        # Footer / Hotkeys
        safe_addstr(stdscr, max_y - 2, 0, "─" * (max_x - 1))
        if prompt_mode:
            prompt_str = f" Steer Hint: {input_buffer}█"
            safe_addstr(stdscr, max_y - 1, 0, prompt_str, curses.A_STANDOUT)
        else:
            footer = " [q] Quit  [p] Pause  [r] Resume  [s] Inject Steer Hint"
            safe_addstr(stdscr, max_y - 1, 0, footer, curses.A_DIM)

        stdscr.refresh()

        # Keyboard event handling
        try:
            ch = stdscr.getch()
        except curses.error:
            continue

        if ch == -1:
            continue

        if prompt_mode:
            if ch in (10, 13):  # Enter key
                if input_buffer.strip() and session_dir:
                    submit_steer_hint(session_dir, input_buffer.strip())
                prompt_mode = False
                input_buffer = ""
            elif ch in (27,):  # ESC key
                prompt_mode = False
                input_buffer = ""
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                input_buffer = input_buffer[:-1]
            elif 32 <= ch <= 126:
                input_buffer += chr(ch)
        else:
            if ch in (ord("q"), ord("Q")):
                break
            elif ch in (ord("p"), ord("P")):
                if session_dir:
                    set_paused_state(session_dir, True)
            elif ch in (ord("r"), ord("R")):
                if session_dir:
                    set_paused_state(session_dir, False)
            elif ch in (ord("s"), ord("S")):
                prompt_mode = True
                input_buffer = ""


def main():
    parser = argparse.ArgumentParser(description="Terminal UI for brave-dev-loop")
    parser.add_argument("--slot", type=int, default=1, help="Run slot number")
    args = parser.parse_args()

    curses.wrapper(lambda stdscr: draw_dashboard(stdscr, slot=args.slot))


if __name__ == "__main__":
    main()
