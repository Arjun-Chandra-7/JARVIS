"""Checking what Iron Man mode needs, before it needs it.

The mode leans on six separate pieces of the desktop, and any one missing produces the same
outcome: most of the sequence runs, one part quietly does not, and the reply still sounds like
success. Everything here exists so that outcome is impossible.
"""
from __future__ import annotations

import pytest

from jarvis.modes import preflight


def _all_present(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _c: "/usr/bin/thing")
    monkeypatch.setattr(preflight, "_reaches", lambda _u, timeout=2.5: True)
    monkeypatch.setattr(preflight, "_an_editor_window", lambda: "x - Visual Studio Code")


def test_a_healthy_machine_says_nothing(monkeypatch):
    """A warning on every activation would be noise, and noise gets ignored."""
    _all_present(monkeypatch)
    assert preflight.blockers() == []
    assert preflight.spoken_warning() == ""


def test_every_dependency_the_sequence_uses_is_checked(monkeypatch):
    _all_present(monkeypatch)
    names = {c.name for c in preflight.check()}
    assert names == {"window lister", "window mover", "terminal", "browser",
                     "editor", "backend", "audio"}


@pytest.mark.parametrize("missing, expect", [
    ("wmctrl", "nothing can be closed"),
    ("xdotool", "will not move into the frame"),
    ("x-terminal-emulator", "terminal pane stays empty"),
    ("google-chrome", "ChatGPT pane stays empty"),
    ("aplay", "runs silently"),
])
def test_each_missing_piece_names_what_stops_working(monkeypatch, missing, expect):
    """Not "wmctrl not found" — what the person will actually notice."""
    _all_present(monkeypatch)
    monkeypatch.setattr(preflight.shutil, "which",
                        lambda c: None if c == missing else "/usr/bin/thing")
    said = preflight.spoken_warning()
    assert expect in said
    assert said.startswith("Heads up")


def test_no_editor_open_is_reported_as_an_empty_main_pane(monkeypatch):
    _all_present(monkeypatch)
    monkeypatch.setattr(preflight, "_an_editor_window", lambda: None)
    assert "nothing to put in the main pane" in preflight.spoken_warning()


def test_a_dead_backend_explains_the_empty_gauges(monkeypatch):
    """The frame comes up either way; without the backend it is a frame around nothing."""
    _all_present(monkeypatch)
    monkeypatch.setattr(preflight, "_reaches", lambda _u, timeout=2.5: False)
    assert "empty gauges" in preflight.spoken_warning()


def test_several_missing_pieces_are_said_in_one_breath(monkeypatch):
    _all_present(monkeypatch)
    monkeypatch.setattr(preflight.shutil, "which",
                        lambda c: None if c in ("aplay", "google-chrome") else "/usr/bin/x")
    said = preflight.spoken_warning()
    assert said.count(";") >= 1
    assert said.endswith(".")


def test_the_warning_never_lists_more_than_three(monkeypatch):
    """A sentence naming seven broken things is not a sentence anybody listens to."""
    monkeypatch.setattr(preflight.shutil, "which", lambda _c: None)
    monkeypatch.setattr(preflight, "_reaches", lambda _u, timeout=2.5: False)
    monkeypatch.setattr(preflight, "_an_editor_window", lambda: None)
    assert preflight.spoken_warning().count(";") <= 2


def test_the_written_report_marks_what_is_missing(monkeypatch):
    _all_present(monkeypatch)
    monkeypatch.setattr(preflight.shutil, "which",
                        lambda c: None if c == "aplay" else "/usr/bin/x")
    report = preflight.report()
    assert "MISSING" in report and "audio" in report
    assert "will run, partially" in report


def test_the_report_is_plain_when_all_is_well(monkeypatch):
    _all_present(monkeypatch)
    assert "Everything Iron Man mode needs is present." in preflight.report()
    assert "MISSING" not in preflight.report()


def test_checking_changes_nothing(monkeypatch):
    """Read-only by construction: a preflight that closed a window to see if it could would be
    worse than no preflight."""
    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: pytest.fail(f"preflight shelled out: {a}"))
    monkeypatch.setattr(preflight, "_reaches", lambda _u, timeout=2.5: True)
    monkeypatch.setattr(preflight, "_an_editor_window", lambda: "code")
    preflight.check()
