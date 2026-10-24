"""Unit tests for scripts/steer-loop.py and steering CLI."""

import os

from scripts.lib.event_stream import (
    get_control_state,
    is_session_paused,
    set_paused_state,
    submit_steer_hint,
)


def test_submit_and_read_steer_hint(tmp_path):
    session_dir = str(tmp_path / "session_steer_cli")
    os.makedirs(session_dir, exist_ok=True)

    # Submit hint
    assert (
        submit_steer_hint(session_dir, "Verify line 42 in component X", author="alice")
        is True
    )

    state = get_control_state(session_dir)
    assert len(state["steer_hints"]) == 1
    hint_obj = state["steer_hints"][0]
    assert hint_obj["hint"] == "Verify line 42 in component X"
    assert hint_obj["author"] == "alice"
    assert hint_obj["consumed"] is False


def test_pause_and_resume_toggle(tmp_path):
    session_dir = str(tmp_path / "session_pause_test")
    os.makedirs(session_dir, exist_ok=True)

    assert is_session_paused(session_dir) is False

    set_paused_state(session_dir, True)
    assert is_session_paused(session_dir) is True

    set_paused_state(session_dir, False)
    assert is_session_paused(session_dir) is False
