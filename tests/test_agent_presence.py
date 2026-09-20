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


@pytest.fixture
def clock(monkeypatch):
    """A clock the test moves itself.

    Several of these rules are about duration — a run too short to have been a task, a green dot
    going stale — and a test that cannot move time can only express them by sleeping.
    """
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(presence, "_now", lambda: t["now"])
    return lambda seconds: t.__setitem__("now", t["now"] + seconds)


def a_real_run(monkeypatch, clock, pid=1):
    """Put the agent through a run long enough to count as work, and stop it."""
    monkeypatch.setattr(presence, "_busy", lambda _pid: 40.0)
    presence.look()
    clock(presence.MIN_TASK_S + 1)
    monkeypatch.setattr(presence, "_busy", lambda _pid: 0.0)


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


def test_an_agent_waiting_on_a_person_is_red(monkeypatch, clock):
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="codex", pid=1)])
    a_real_run(monkeypatch, clock)
    state = presence.look(lambda: "Do you want to proceed? (y/n)")
    assert (state.kind, state.colour) == ("asking", "red")


def test_an_agent_that_stopped_without_a_question_is_green(monkeypatch, clock):
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="claude", pid=1)])
    a_real_run(monkeypatch, clock)
    state = presence.look(lambda: "All done. tokens used 4,201")
    assert (state.kind, state.colour) == ("done", "green")
    first_id = state.completion_id
    # A short process-list gap must not create a second completion identity for the same cycle.
    monkeypatch.setattr(presence, "_processes", lambda: [])
    presence.look()
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="claude", pid=1)])
    state = presence.look(lambda: "All done. tokens used 4,201")
    assert state.completion_id == first_id


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


def test_a_finished_agent_goes_quiet_once_the_news_is_old(monkeypatch, clock):
    """Green means "just finished". An agent left open all afternoon is not news."""
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="claude", pid=1)])
    a_real_run(monkeypatch, clock)
    assert presence.look(lambda: "done").kind == "done"
    clock(presence.DONE_FOR_S + 1)
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


def test_previously_running_agent_is_chosen_over_arbitrary_idle_agent(monkeypatch, clock):
    """When an agent stops and all open agents have 0% CPU, the one that was just working
    must be the one reported as done, regardless of PID ordering in ps."""
    procs = [
        presence.Agent(name="codex", pid=10),
        presence.Agent(name="claude", pid=20),
    ]
    monkeypatch.setattr(presence, "_processes", lambda: procs)
    # Claude is busy working, Codex is idle
    monkeypatch.setattr(presence, "_busy", lambda pid: 25.0 if pid == 20 else 0.0)
    running = presence.look()
    assert (running.kind, running.agent) == ("running", "claude")

    # Claude stops; both processes now report 0.0 CPU
    clock(presence.MIN_TASK_S + 1)
    monkeypatch.setattr(presence, "_busy", lambda _pid: 0.0)
    stopped = presence.look(lambda: "Done.")
    assert (stopped.kind, stopped.agent) == ("done", "claude")
    assert stopped.completion_id.startswith("claude:20:")


def test_roster_canonical_and_spoken_names():
    from jarvis.coding import roster
    assert roster.canonical_name("Antigravity") == "agy"
    assert roster.canonical_name("agy") == "agy"
    assert roster.canonical_name("gemini") == "agy"
    assert roster.canonical_name("Claude") == "claude"
    assert roster.canonical_name("codex") == "codex"
    assert roster.canonical_name(None) == ""

    assert roster.spoken_name("agy") == "Antigravity"
    assert roster.spoken_name("Antigravity") == "Antigravity"
    assert roster.spoken_name("claude") == "Claude"
    assert roster.spoken_name("codex") == "Codex"
    assert roster.spoken_name(None) == "The agent"


# --------------------------------------------------------------------------- what is a session
#
# Measured on the machine this was written for: ten processes were named claude or codex and only
# three were sessions. The rest were a daemon, three pty hosts, an unclaimed pre-warmed spare and
# the ChatGPT extension's app-server. Counting them is what broke the dot — a spare holding
# 12-15% of a core kept it orange all day, and the first reading after a restart announced that
# Codex had finished about a server that had never run anything.

def _ps_listing(rows):
    """A fake `ps -eo pid,comm,args`."""
    class _Out:
        stdout = "  PID COMMAND         ARGS\n" + "".join(
            f"{pid:5} {comm:15} {args}\n" for pid, comm, args in rows)
    return _Out


@pytest.mark.parametrize("pid,comm,args", [
    (1, "claude.exe", "/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe "
                      "daemon run --origin transient"),
    (2, "claude.exe", "claude bg-pty-host --bg-pty-host /tmp/cc-daemon-1000/x/spare/a.pty.sock"),
    (3, "codex", "/home/u/.vscode/extensions/openai.chatgpt/bin/codex app-server "
                 "--analytics-default-enabled"),
])
def test_the_plumbing_is_not_a_session(monkeypatch, pid, comm, args):
    """Claude keeps a daemon and a pool of pty hosts alive; the ChatGPT extension keeps an
    app-server. They carry the same command name as a session and burn processor time of their
    own, and none of them is something a person is talking to."""
    monkeypatch.setattr(presence.subprocess, "run",
                        lambda *a, **k: _ps_listing([(pid, comm, args)])())
    monkeypatch.setattr(presence, "_working_directory", lambda _pid: "/home/u/project")
    assert presence._processes() == []


def test_a_spare_is_a_session_once_it_has_been_claimed(monkeypatch):
    """The awkward one. A claimed spare *is* the session you are typing into — this very file was
    written by one. What separates it from an unclaimed spare is that the unclaimed one is still
    sitting in the daemon's scratch directory while a claimed one has moved to your project."""
    rows = [(11, "claude.exe", "claude bg-spare --bg-spare /tmp/cc-daemon-1000/x/spare/a.sock"),
            (12, "claude.exe", "claude bg-spare --bg-spare /tmp/cc-daemon-1000/x/spare/b.sock")]
    monkeypatch.setattr(presence.subprocess, "run", lambda *a, **k: _ps_listing(rows)())
    monkeypatch.setattr(presence, "_working_directory",
                        lambda pid: "/home/u/project" if pid == 11 else "/tmp/cc-daemon-1000/x/spare")
    assert [a.pid for a in presence._processes()] == [11]


def test_a_directory_we_cannot_read_does_not_hide_a_session(monkeypatch):
    """Rejecting on the working directory must never be able to make a real session invisible."""
    rows = [(13, "claude", "claude")]
    monkeypatch.setattr(presence.subprocess, "run", lambda *a, **k: _ps_listing(rows)())
    monkeypatch.setattr(presence, "_working_directory", lambda _pid: "")
    assert [a.pid for a in presence._processes()] == [13]


# --------------------------------------------------------------------------- false completions

def test_an_agent_merely_sitting_open_has_not_finished(monkeypatch, clock):
    """The first look used to report "done" for whatever process came first, so every restart
    greeted you with news about a session that had done nothing."""
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="codex", pid=1)])
    monkeypatch.setattr(presence, "_busy", lambda _pid: 0.0)
    reads = []
    for _ in range(3):
        state = presence.look(lambda: reads.append(1) or "")
        assert state.kind == "idle", "announced a completion for an agent that never ran"
        clock(4)
    assert reads == [], "read the terminal about an agent that has done nothing"


def test_one_busy_sample_is_a_twitch_not_a_task(monkeypatch, clock):
    """An idle session was measured crossing the threshold for a single sample. Treated as a run,
    that became an orange dot and then "Claude has finished, sir" about nothing at all."""
    monkeypatch.setattr(presence, "_processes",
                        lambda: [presence.Agent(name="claude", pid=1)])
    monkeypatch.setattr(presence, "_busy", lambda _pid: presence.WORKING_ABOVE + 1)
    presence.look()
    clock(1.0)                                   # less than MIN_TASK_S
    monkeypatch.setattr(presence, "_busy", lambda _pid: 0.0)
    state = presence.look(lambda: "All done. tokens used 4,201")
    assert state.kind == "idle", "a one-sample twitch was reported as a finished task"
    assert not state.completion_id


# --------------------------------------------------------------------------- several at once

def test_a_session_finishing_is_seen_while_another_is_still_working(monkeypatch, clock):
    """Watching only the busiest process meant that while any session worked, a different one
    finishing was invisible — and someone with three terminals open has three sessions."""
    procs = [presence.Agent(name="claude", pid=10), presence.Agent(name="codex", pid=20)]
    monkeypatch.setattr(presence, "_processes", lambda: procs)
    monkeypatch.setattr(presence, "_busy", lambda _pid: 40.0)
    presence.look()                                          # both working
    clock(presence.MIN_TASK_S + 1)
    # Codex stops; Claude carries on.
    monkeypatch.setattr(presence, "_busy", lambda pid: 40.0 if pid == 10 else 0.0)
    state = presence.look(lambda: "All done. tokens used 12")
    assert (state.kind, state.agent) == ("done", "codex")
    assert [(s.name, s.kind) for s in presence.sessions()] == [("claude", "running"),
                                                               ("codex", "done")]


def test_a_question_outranks_another_session_working(monkeypatch, clock):
    """Red is the one that means you are being waited on. It must not be hidden by a second
    session that happens to be busy."""
    procs = [presence.Agent(name="claude", pid=10), presence.Agent(name="codex", pid=20)]
    monkeypatch.setattr(presence, "_processes", lambda: procs)
    monkeypatch.setattr(presence, "_busy", lambda _pid: 40.0)
    presence.look()
    clock(presence.MIN_TASK_S + 1)
    monkeypatch.setattr(presence, "_busy", lambda pid: 40.0 if pid == 10 else 0.0)
    state = presence.look(lambda: "Do you want to proceed? (y/n)")
    assert (state.kind, state.agent, state.colour) == ("asking", "codex", "red")
