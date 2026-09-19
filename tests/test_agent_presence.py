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
    "Continue? (y/n)",
    "Overwrite file? [y/N]",
    "❯ 1. Yes",
    "  2. No, and tell Claude what to do differently",
    "Press Enter to continue",
    "Shall I apply the patch?",
])
def test_these_read_as_a_question(screen):
    assert presence.classify_words(screen) == "asking"


@pytest.mark.parametrize("screen", [
    "The warnings name the symptom, not the binary, and ask permission first",
    "I need to confirm the fix works before committing",
    "red when one is waiting on you, and allow the agent to continue",
    "Do you want to proceed with this refactor is a question I considered at length",
    "Added a permission prompt to the installer and approved the change",
])
def test_prose_about_permission_is_not_a_permission_prompt(screen):
    """The first version matched vocabulary — allow, approve, permission, confirm — and went red
    at the mere mention of them. Terminals are full of prose about permissions: commit messages,
    diffs, this project's own source. The dot sat red while nothing was being asked."""
    assert presence.classify_words(screen) != "asking"


def test_only_the_bottom_of_the_screen_counts():
    """A prompt answered five minutes ago is still in the scrollback. Only the live one matters."""
    old_prompt = "Do you want to proceed?\n" + "\n".join(f"line {i}" for i in range(20))
    assert presence.classify_words(old_prompt) != "asking"


def test_a_prompt_at_the_bottom_is_caught_under_real_output():
    screen = "\n".join([
        "Reading files...",
        "Applying the patch to jarvis/modes/ironman.py",
        "",
        "Do you want to proceed?",
        "❯ 1. Yes",
        "  2. No",
    ])
    assert presence.classify_words(screen) == "asking"


def test_a_long_line_is_a_paragraph_not_a_prompt():
    long_one = "Shall I " + "x" * 120 + "?"
    assert presence.classify_words(long_one) != "asking"


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
