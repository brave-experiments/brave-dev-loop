"""Unit tests for scripts/lib/event_stream.py."""

import json

from scripts.lib.event_stream import (
    EventType,
    LoopEvent,
    StreamParser,
    emit_event,
    get_control_state,
    is_session_paused,
    pop_pending_steer_hints,
    read_events,
    set_paused_state,
    submit_steer_hint,
    tail_events,
)


def test_loop_event_serialization():
    ev = LoopEvent(
        event_type=EventType.PHASE_CHANGE,
        story_id="US-101",
        slot=2,
        session_id="20261013T000000Z-slot-2",
        payload={"phase": "IMPLEMENTATION"},
    )
    json_str = ev.to_json()
    parsed = LoopEvent.from_json(json_str)

    assert parsed is not None
    assert parsed.event_type == EventType.PHASE_CHANGE
    assert parsed.story_id == "US-101"
    assert parsed.slot == 2
    assert parsed.payload == {"phase": "IMPLEMENTATION"}


def test_emit_and_read_events(tmp_path):
    session_dir = str(tmp_path / "session_1")

    ev1 = LoopEvent(event_type=EventType.SESSION_START, story_id="S1")
    ev2 = LoopEvent(event_type=EventType.TOOL_CALL, payload={"tool": "Bash"})

    assert emit_event(session_dir, ev1) is True
    assert emit_event(session_dir, ev2) is True

    events = read_events(session_dir)
    assert len(events) == 2
    assert events[0].event_type == EventType.SESSION_START
    assert events[1].event_type == EventType.TOOL_CALL
    assert events[1].payload == {"tool": "Bash"}

    # Test limit parameter
    recent = read_events(session_dir, limit=1)
    assert len(recent) == 1
    assert recent[0].event_type == EventType.TOOL_CALL


def test_tail_events(tmp_path):
    session_dir = str(tmp_path / "session_tail")
    ev = LoopEvent(event_type=EventType.GATE_STATUS, payload={"status": "PASS"})
    emit_event(session_dir, ev)

    gen = tail_events(session_dir, follow=False)
    events = list(gen)
    assert len(events) == 1
    assert events[0].event_type == EventType.GATE_STATUS


def test_control_state_and_steering(tmp_path):
    session_dir = str(tmp_path / "session_steer")

    # Initial state
    state = get_control_state(session_dir)
    assert state.get("paused") is False
    assert state.get("steer_hints") == []

    # Pause and resume
    assert set_paused_state(session_dir, True) is True
    assert is_session_paused(session_dir) is True

    assert set_paused_state(session_dir, False) is True
    assert is_session_paused(session_dir) is False

    # Submit hints
    assert submit_steer_hint(session_dir, "Do not touch header X") is True
    assert submit_steer_hint(session_dir, "Focus on unit test Y") is True

    # Pop pending hints
    pending = pop_pending_steer_hints(session_dir)
    assert len(pending) == 2
    assert pending[0] == "Do not touch header X"
    assert pending[1] == "Focus on unit test Y"

    # Second pop should be empty (already consumed)
    second_pop = pop_pending_steer_hints(session_dir)
    assert second_pop == []


def test_stream_parser(tmp_path):
    session_dir = str(tmp_path / "session_parser")
    parser = StreamParser(session_dir=session_dir, slot=1, story_id="US-50")

    # Tool call JSON
    tool_line = json.dumps(
        {
            "type": "tool_use",
            "name": "Edit",
            "input": {"file": "test.cc"},
        }
    )
    ev1 = parser.parse_line(tool_line)
    assert ev1 is not None
    assert ev1.event_type == EventType.TOOL_CALL
    assert ev1.payload["tool"] == "Edit"

    # Diff header line
    diff_line = "diff --git a/foo.cc b/foo.cc"
    ev2 = parser.parse_line(diff_line)
    assert ev2 is not None
    assert ev2.event_type == EventType.FILE_DIFF

    # Gate status line
    gate_line = "[✓] Tests passed cleanly"
    ev3 = parser.parse_line(gate_line)
    assert ev3 is not None
    assert ev3.event_type == EventType.GATE_STATUS

    # Read back all emitted events
    all_events = read_events(session_dir)
    assert len(all_events) == 3
