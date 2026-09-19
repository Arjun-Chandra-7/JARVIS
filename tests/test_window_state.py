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
