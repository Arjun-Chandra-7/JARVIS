"""Whether a coding agent is working, waiting on you, finished, or not there.

Answers what the dot on the pill is for, and works whether Jarvis started the agent or the user
opened it themselves — it watches the machine rather than remembering what it launched.
"""
from __future__ import annotations

import pytest

from jarvis.coding import presence


@pytest.fixture(autouse=True)
def _clean():
    presence.reset()
    yield
    presence.reset()


def test_nothing_running_is_blue_and_shows_no_dot(monkeypatch):
    """Blue means "no news", and the pill shows nothing rather than a colour for it."""
    monkeypatch.setattr(presence, "_processes", lambda: [])
    state = presence.look()
    assert state.kind == "idle" and state.colour == "blue"


def test_a_busy_agent_is_orange(monkeypatch):
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="claude", pid=1)])
    monkeypatch.setattr(presence, "_busy", lambda _pid: 40.0)
    state = presence.look()
    assert (state.kind, state.colour, state.agent) == ("running", "orange", "claude")


def test_an_agent_waiting_on_a_person_is_red(monkeypatch):
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="codex", pid=1)])
    monkeypatch.setattr(presence, "_busy", lambda _pid: 40.0)
    presence.look()                                    # working
    monkeypatch.setattr(presence, "_busy", lambda _pid: 0.0)
    state = presence.look(lambda: "Do you want to proceed? (y/n)")
    assert (state.kind, state.colour) == ("asking", "red")


def test_an_agent_that_stopped_without_a_question_is_green(monkeypatch):
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="claude", pid=1)])
    monkeypatch.setattr(presence, "_busy", lambda _pid: 40.0)
    presence.look()
    monkeypatch.setattr(presence, "_busy", lambda _pid: 0.0)
    state = presence.look(lambda: "All done. tokens used 4,201")
    assert (state.kind, state.colour) == ("done", "green")


@pytest.mark.parametrize("screen", [
    "Do you want to proceed?",
    "Allow Claude to edit this file?",
    "(y/n)",
    "[y/N]",
    "❯ 1. Yes",
    "Press Enter to continue",
])
def test_these_read_as_a_question(screen):
    assert presence.classify_words(screen) == "asking"


@pytest.mark.parametrize("screen", [
    "tokens used 1,234",
    "Completed the refactor.",
    "xor_sensei@Bhramastra:~/Dev$ ",
])
def test_these_read_as_finished(screen):
    assert presence.classify_words(screen) == "done"


def test_the_terminal_is_only_read_when_it_can_say_something_new(monkeypatch):
    """Reading it borrows the clipboard; doing that every few seconds is a tax on everything
    else the user copies."""
    reads = []
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="claude", pid=1)])
    monkeypatch.setattr(presence, "_busy", lambda _pid: 40.0)
    presence.look(lambda: reads.append(1) or "")
    presence.look(lambda: reads.append(1) or "")
    assert reads == [], "read the terminal while the agent was plainly working"


def test_a_finished_agent_goes_quiet_once_the_news_is_old(monkeypatch):
    """Green means "just finished". An agent left open all afternoon is not news."""
    import time
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="claude", pid=1)])
    monkeypatch.setattr(presence, "_busy", lambda _pid: 40.0)
    presence.look()
    monkeypatch.setattr(presence, "_busy", lambda _pid: 0.0)
    assert presence.look(lambda: "done").kind == "done"
    presence._stopped_at = time.time() - presence.DONE_FOR_S - 1
    assert presence.look(lambda: "done").kind == "idle"


def test_a_shell_that_merely_mentions_an_agent_is_not_an_agent(monkeypatch):
    """Otherwise every terminal that has ever typed the word counts as one running — including
    the session writing this."""
    listing = ("  PID COMMAND         ARGS\n"
               "  111 bash            bash -c echo claude is great\n"
               "  222 claude          claude --resume abc\n")
    class _Out:
        stdout = listing
    monkeypatch.setattr(presence.subprocess, "run", lambda *a, **k: _Out())
    found = presence._processes()
    assert [a.pid for a in found] == [222]


def test_every_state_has_a_colour():
    for kind in ("idle", "running", "asking", "done"):
        assert presence.State(kind=kind).colour in ("blue", "orange", "red", "green")
