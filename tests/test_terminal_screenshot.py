"""Tests for terminal-screenshot.py.

The tool exists so a body can show the screen a person saw rather than describe
it, so what these pin is fidelity: the same bytes a terminal was sent have to
produce the same grid, and anything the replay does not model has to be said out
loud instead of quietly changing the picture.

`golden/bravebot-tui-capture.txt` is a real capture of bravebot's interface,
taken through its own `contrib/drive_tui.py --raw`. Its expected screen was
checked against `pyte`, an independent terminal emulator, and matched byte for
byte.
"""

import importlib.util
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), os.pardir, "scripts", "terminal-screenshot.py"
)
GOLDEN = os.path.join(os.path.dirname(__file__), "golden")

ESC = "\x1b"


@pytest.fixture(scope="module")
def shot():
    spec = importlib.util.spec_from_file_location("terminal_screenshot", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def screen_of(shot, capture, cols=20, rows=4):
    return shot.replay(capture, cols, rows).display()


# ── The reason the tool exists ───────────────────────────────────────────────
def test_a_redrawn_cell_shows_its_last_value(shot):
    """Overwriting is the whole difficulty: the bytes hold both values and the
    screen holds one. Stripping escapes would show 'oldnew'."""
    capture = f"old{ESC}[1;1Hnew"
    assert screen_of(shot, capture) == ["new"]


def test_a_box_drawn_out_of_order_still_lands_square(shot):
    """A full-screen program paints wherever it likes. The characters arrive in
    an order that makes no sense as a stream and every sense as a picture."""
    capture = f"{ESC}[2;1H|b|{ESC}[1;1H+-+{ESC}[3;1H+-+"
    assert screen_of(shot, capture) == ["+-+", "|b|", "+-+"]


def test_the_real_interface_renders_the_screen_it_drew(shot):
    """The capture is bravebot answering a trust prompt and running a shell
    command. Read as a stream it is unreadable; read as a screen it is what the
    user saw."""
    with open(os.path.join(GOLDEN, "bravebot-tui-capture.txt"), encoding="utf-8") as fh:
        capture = fh.read()
    with open(os.path.join(GOLDEN, "bravebot-tui-screen.txt"), encoding="utf-8") as fh:
        expected = fh.read().splitlines()

    assert screen_of(shot, capture, cols=100, rows=30) == expected

    joined = "\n".join(expected)
    assert "trusting /private/tmp/tuishot/work" in joined
    assert "Ask Brave Bot to do anything" in joined
    # The words are separated, which is the failure of the strip-the-escapes
    # approach this replaces: it produced "Filesherewillbereadastrusted".
    assert "? for shortcuts" in joined


# ── Cursor motion ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "capture,expected",
    [
        (f"ab{ESC}[1Dz", ["az"]),  # left
        (f"a{ESC}[2Cz", ["a  z"]),  # right
        (f"a{ESC}[2;1Hb{ESC}[1Az", ["az", "b"]),  # up
        (f"a{ESC}[1Bz", ["a", " z"]),  # down
        (f"abc{ESC}[1Gz", ["zbc"]),  # to column
        (f"abc{ESC}[2dz", ["abc", "   z"]),  # to row, keeping the column
        ("ab\rz", ["zb"]),  # carriage return
        ("ab\bz", ["az"]),  # backspace
        ("a\nz", ["a", " z"]),  # linefeed keeps the column
    ],
)
def test_cursor_motion(shot, capture, expected):
    assert screen_of(shot, capture) == expected


def test_a_default_parameter_means_one(shot):
    """`ESC[C` with no number moves one column, not zero."""
    assert screen_of(shot, f"a{ESC}[Cz") == ["a z"]


def test_cursor_position_defaults_to_the_top_left(shot):
    assert screen_of(shot, f"abc{ESC}[Hz") == ["zbc"]


def test_the_cursor_cannot_leave_the_screen(shot):
    """Clamping rather than wrapping: a program that asks for row 99 of a
    4-row terminal gets the last row, which is what the terminal does."""
    assert screen_of(shot, f"{ESC}[99;99Hz") == ["", "", "", " " * 19 + "z"]


# ── Erasing ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "capture,expected",
    [
        (f"abcd{ESC}[1;3H{ESC}[K", ["ab"]),  # to end of line
        (f"abcd{ESC}[1;3H{ESC}[1K", ["   d"]),  # to start of line
        (f"abcd{ESC}[2K", []),  # the whole line, leaving nothing to show
        (f"ab{ESC}[2;1Hcd{ESC}[1;3H{ESC}[J", ["ab"]),  # to end of screen
        (f"ab{ESC}[2;1Hcd{ESC}[2J", []),  # the whole screen
    ],
)
def test_erasing(shot, capture, expected):
    assert screen_of(shot, capture) == expected


# ── Wrapping and scrolling ───────────────────────────────────────────────────
def test_text_wraps_at_the_right_margin(shot):
    assert screen_of(shot, "abcde", cols=3, rows=2) == ["abc", "de"]


def test_the_top_line_is_lost_when_the_screen_scrolls(shot):
    """A capture longer than the terminal is tall ends where the terminal
    would: showing the bottom, having scrolled the top away."""
    assert screen_of(shot, "a\r\nb\r\nc\r\nd", cols=3, rows=3) == ["b", "c", "d"]


# ── What is not modelled is said out loud ────────────────────────────────────
def test_colour_is_dropped_without_complaint(shot):
    """A text screenshot carries no palette, and saying so on every SGR would
    make the warning useless."""
    screen = shot.replay(f"{ESC}[38;2;255;64;0;49mwarm{ESC}[0m", 20, 2)
    assert screen.display() == ["warm"]
    assert screen.unmodelled == {}


@pytest.mark.parametrize(
    "sequence",
    [
        f"{ESC}[?25l",  # hide the cursor
        f"{ESC}[?1049h",  # enter the alternate screen
        f"{ESC}[?1000h",  # mouse reporting
        f"{ESC}[?2004h",  # bracketed paste
        f"{ESC}[?u",  # keyboard protocol
        f"{ESC}[c",  # ask the terminal what it is
        f"{ESC}]0;a title{ESC}\\",  # set the window title
        f"{ESC}(B",  # select a character set
    ],
)
def test_a_sequence_that_changes_no_cell_is_silent(shot, sequence):
    """These are the bulk of what a real capture holds. Warning about them
    would train everyone to ignore the warning."""
    screen = shot.replay(f"{sequence}kept", 20, 2)
    assert screen.display() == ["kept"]
    assert screen.unmodelled == {}


def test_the_alternate_screen_is_not_left_on_exit(shot):
    """A program returns to the primary screen as it exits. Honouring that
    would blank the frame the screenshot is of."""
    screen = shot.replay(f"{ESC}[?1049hthe frame{ESC}[?1049l", 20, 2)
    assert screen.display() == ["the frame"]


def test_an_unmodelled_sequence_is_reported(shot):
    """Silence here would pass off a wrong screen as a right one. `ESC[u`
    restores a saved cursor, which this does not track."""
    screen = shot.replay(f"a{ESC}[uz", 20, 2)
    assert screen.unmodelled == {f"{ESC}[u": 1}


def test_selective_erase_is_reported_rather_than_guessed(shot):
    """It clears only cells left unprotected by an attribute this does not
    track, so the honest answer is to say the screen may be wrong."""
    screen = shot.replay(f"abc{ESC}[?K", 20, 2)
    assert screen.unmodelled == {f"{ESC}[?K": 1}


def test_an_unknown_sequence_does_not_leak_into_the_screen(shot):
    """Its parameters must not be printed as text -- that is the bug the
    strip-the-escapes approach had."""
    screen = shot.replay(f"{ESC}[38;5;9Zkept", 20, 2)
    assert screen.display() == ["kept"]
    assert screen.unmodelled


# ── The command line ─────────────────────────────────────────────────────────
def _run(args, stdin=None):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        input=stdin,
        capture_output=True,
        text=True,
    )


def test_the_screen_goes_to_stdout_and_the_doubt_to_stderr(tmp_path):
    """So `terminal-screenshot.py … > screen.txt` never captures a warning as
    though it were part of the screen."""
    capture = tmp_path / "capture.txt"
    capture.write_text(f"a{ESC}[uz", encoding="utf-8")
    done = _run([str(capture), "--cols", "20", "--rows", "2"])
    assert done.returncode == 0
    assert "z" in done.stdout
    assert "not modelled" in done.stderr


def test_strict_fails_when_something_was_not_modelled(tmp_path):
    capture = tmp_path / "capture.txt"
    capture.write_text(f"a{ESC}[uz", encoding="utf-8")
    assert (
        _run([str(capture), "--cols", "20", "--rows", "2", "--strict"]).returncode == 1
    )


def test_strict_passes_on_the_real_capture():
    """The interface emits nothing this cannot account for, so a bravebot
    screenshot can be taken under --strict and trusted."""
    done = _run(
        [
            os.path.join(GOLDEN, "bravebot-tui-capture.txt"),
            "--cols",
            "100",
            "--rows",
            "30",
            "--strict",
        ]
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert "Ask Brave Bot to do anything" in done.stdout


def test_a_capture_arrives_on_stdin():
    done = _run(["-", "--cols", "20", "--rows", "2"], stdin=f"old{ESC}[1;1Hnew")
    assert done.returncode == 0
    assert done.stdout.strip() == "new"
