"""Putting a window back the way it was found.

From the log, verbatim — the user armed the mode and left it ten minutes later:

    you (voice)> Let's go Iron Man mode.
    jarvis>      Iron Man mode activated. Laid out editor, chatgpt.
    you (voice)> back to normal mode.
    jarvis>      Back to normal, sir. That was 10 minutes in the workshop.

The editor did not go back. It said it had, and left a full-screen window floating at 1655x937.
"""
from __future__ import annotations

import pytest

from jarvis.modes import ironman


def _xprop(monkeypatch, state_line: str):
    class _Out:
        stdout = state_line
    monkeypatch.setattr(ironman.subprocess, "run", lambda *a, **k: _Out())


def test_a_fullscreen_window_is_not_read_as_an_ordinary_one(monkeypatch):
    """This is the whole bug. Fullscreen and maximised are different X11 states, and reading only
    the second recorded a full-screen editor as "not maximised"."""
    _xprop(monkeypatch, "_NET_WM_STATE(ATOM) = _NET_WM_STATE_FULLSCREEN, _NET_WM_STATE_FOCUSED")
    assert ironman._window_state("0x1") == "fullscreen"


def test_a_maximised_window_still_reads_as_maximised(monkeypatch):
    _xprop(monkeypatch, "_NET_WM_STATE(ATOM) = _NET_WM_STATE_MAXIMIZED_HORZ, "
                        "_NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_FOCUSED")
    assert ironman._window_state("0x1") == "maximized"


def test_an_ordinary_window_reads_as_neither(monkeypatch):
    _xprop(monkeypatch, "_NET_WM_STATE(ATOM) = _NET_WM_STATE_FOCUSED")
    assert ironman._window_state("0x1") == ""


def test_fullscreen_wins_when_a_window_claims_both(monkeypatch):
    """That is what the person is actually looking at."""
    _xprop(monkeypatch, "_NET_WM_STATE(ATOM) = _NET_WM_STATE_MAXIMIZED_VERT, "
                        "_NET_WM_STATE_FULLSCREEN")
    assert ironman._window_state("0x1") == "fullscreen"


def test_an_unreadable_window_is_treated_as_ordinary(monkeypatch):
    def explode(*a, **k):
        raise OSError("no xprop")
    monkeypatch.setattr(ironman.subprocess, "run", explode)
    assert ironman._window_state("0x1") == ""


@pytest.mark.parametrize("state, expect_add", [
    ("fullscreen", "add,fullscreen"),
    ("maximized", "add,maximized_vert,maximized_horz"),
])
def test_restoring_puts_the_state_back(monkeypatch, state, expect_add):
    calls = []
    monkeypatch.setattr(ironman.subprocess, "run",
                        lambda a, **k: calls.append(" ".join(a)) or type("R", (), {"returncode": 0})())
    ironman._set_window_state("0x1", state)
    assert any(expect_add in c for c in calls)


def test_both_states_are_cleared_before_either_is_applied(monkeypatch):
    """A window left fullscreen ignores a move; one left maximised snaps back later."""
    calls = []
    monkeypatch.setattr(ironman.subprocess, "run",
                        lambda a, **k: calls.append(" ".join(a)) or type("R", (), {"returncode": 0})())
    ironman._set_window_state("0x1", "maximized")
    assert any("remove,fullscreen" in c for c in calls)
    assert any("remove,maximized_vert,maximized_horz" in c for c in calls)
    assert calls.index("wmctrl -i -r 0x1 -b remove,fullscreen") < \
           max(i for i, c in enumerate(calls) if "add," in c)


def test_an_ordinary_window_is_left_ordinary(monkeypatch):
    calls = []
    monkeypatch.setattr(ironman.subprocess, "run",
                        lambda a, **k: calls.append(" ".join(a)) or type("R", (), {"returncode": 0})())
    ironman._set_window_state("0x1", "")
    assert not any("add," in c for c in calls)


def test_a_recovery_file_written_by_the_older_build_still_restores(monkeypatch):
    """Those stored a bool. A session interrupted by an upgrade must still get its windows back."""
    placed, states = [], []
    monkeypatch.setattr(ironman, "_windows", lambda: [("0x1", "Editor"), ("0x2", "Other")])
    monkeypatch.setattr(ironman, "_place_window", lambda wid, *r: placed.append((wid, r)) or True)
    monkeypatch.setattr(ironman, "_set_window_state", lambda wid, st: states.append((wid, st)))
    monkeypatch.setattr(ironman.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())

    ironman._restore_surfaces((("0x1", "Editor", (0, 0, 100, 100), True),), ())
    assert states == [("0x1", "maximized")]

    states.clear()
    ironman._restore_surfaces((("0x2", "Other", (0, 0, 100, 100), False),), ())
    assert states == [("0x2", "")]


def test_the_new_shape_restores_fullscreen(monkeypatch):
    states = []
    monkeypatch.setattr(ironman, "_windows", lambda: [("0x1", "Editor")])
    monkeypatch.setattr(ironman, "_place_window", lambda wid, *r: True)
    monkeypatch.setattr(ironman, "_set_window_state", lambda wid, st: states.append((wid, st)))
    monkeypatch.setattr(ironman.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    ironman._restore_surfaces((("0x1", "Editor", (0, 0, 100, 100), "fullscreen"),), ())
    assert states == [("0x1", "fullscreen")]


# ------------------------------------------------------- not guessing when there is no record
def test_without_a_snapshot_nothing_is_moved(monkeypatch):
    """This used to un-maximise the editor and resize it to 88% of the screen with a margin. It
    is why a full-screen editor came back as 1655x937 on a 1920x1080 screen: 88% of 1920 is 1689
    and 6% of 1920 is 115, which is exactly where it was found."""
    def explode(*a, **k):
        pytest.fail(f"touched the desktop with no record of how it was: {a}")
    monkeypatch.setattr(ironman.subprocess, "run", explode)
    ironman._rescue_editor_without_snapshot()


def test_leaving_the_mode_says_so_when_it_could_not_restore(monkeypatch):
    """Saying "back to normal" while leaving the windows wherever they landed is the false
    report this whole project exists to stop."""
    import asyncio
    from jarvis import mode_command

    monkeypatch.setattr(ironman, "_on", object())
    monkeypatch.setattr(ironman, "deactivate",
                        lambda: {"was_on": True, "restored": False, "minutes": 10})
    monkeypatch.setattr(ironman, "on", lambda: True)

    async def quiet(_shape):
        return None
    monkeypatch.setattr(mode_command, "_tell_the_overlay", quiet)

    said = asyncio.run(mode_command.handle("jarvis back to normal mode"))
    assert "left them as they are" in said


def test_a_real_restore_does_not_apologise(monkeypatch):
    import asyncio
    from jarvis import mode_command

    monkeypatch.setattr(ironman, "deactivate",
                        lambda: {"was_on": True, "restored": True, "minutes": 3})
    monkeypatch.setattr(ironman, "on", lambda: True)

    async def quiet(_shape):
        return None
    monkeypatch.setattr(mode_command, "_tell_the_overlay", quiet)

    said = asyncio.run(mode_command.handle("jarvis normal mode"))
    assert "left them as they are" not in said and "3 minutes" in said
