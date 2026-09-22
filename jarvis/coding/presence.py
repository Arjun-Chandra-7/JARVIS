"""Whether a coding agent is working, waiting on you, finished, or not there at all.

Answers the question the little dot on the pill is for: is Claude — or Codex, or Antigravity —
doing something right now, and does it need me? It works whether Jarvis started the agent or you
opened it yourself in a terminal, because it watches the machine rather than remembering what it
launched.

State is read from the process, not from the screen. An agent that is thinking burns processor
time; one that has stopped does not, whether it stopped because it finished or because it is
waiting for you to answer something. That single reading separates "working" from "not working"
for nothing, every few seconds, without touching the clipboard or stealing focus.

Telling *why* it stopped does need the words, so the terminal is read — but only on the
transition, once, rather than on every poll. Reading it borrows the clipboard for a moment, and
doing that continuously would be a tax on everything else you copy.

Every session is tracked on its own. Watching only the busiest one meant that while any session
was working, a different one finishing was invisible — and someone with three terminals open has
three sessions.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from . import transcripts

# What each state means to the eye. The dot on the pill takes these directly.
COLOURS = {
    "idle": "blue",         # nothing open
    "running": "orange",    # working
    "asking": "red",        # wants an answer from you
    "done": "green",        # finished, still open
}

AGENTS = ("claude", "codex", "agy")

# Above this share of a core, over one sampling gap, the agent is doing something.
#
# Measured on this machine, sampling every four seconds for a minute: a session actively working
# held 9–20% and never dipped below 9, while sessions sitting at their prompt read 0.5–2.2% with
# one excursion to 4.7%. Five is above everything idle and well below anything working.
WORKING_ABOVE = 5.0

# A run shorter than this was a twitch, not a task, and finishing it is not news. Without this a
# single idle session crossing the threshold for one sample turned into an orange dot and then
# "Claude has finished, sir" about a task nobody asked for.
MIN_TASK_S = 6.0

# How long a finished agent stays green before it is just an open window again.
DONE_FOR_S = 90.0

# A process missing from one listing is a race, not an exit — `ps` and a terminal redraw
# regularly disagree. Its record is kept this long so that coming back does not read as a new
# session with a new completion to announce.
GONE_AFTER_S = 30.0


# --------------------------------------------------------------------------- what is a session
#
# Claude keeps a daemon, a pool of pty hosts, and pre-warmed spare processes alive at all times;
# the ChatGPT extension keeps an app-server. Every one of them carries the same command name as a
# real session and every one of them burns processor time of its own.
#
# Counting them is what broke the dot. Measured here: of the ten processes named claude or codex,
# only three were sessions. The 12–15% of a core that kept the dot orange was a spare, and the
# first reading after a restart announced that Codex had finished — about an app-server that had
# never run anything.
#
# The subcommand names the plumbing, and it is matched as a whole argument rather than as a word
# anywhere in the line. Searching the line for "daemon" looked right and was not: every claimed
# spare carries the daemon's socket path in its arguments, so the pattern threw out the real
# sessions along with the plumbing. Nor is it always the second argument — the editor launches
# the ChatGPT one as `codex -c features.code_mode_host=true app-server`. An exact argument, in
# any position, is the thing that is neither too loose nor too strict.
_PLUMBING_SUBCOMMANDS = {"daemon", "bg-pty-host", "app-server", "mcp"}

# A spare is the awkward one, because a claimed spare *is* the session you are typing into — this
# very file was written by one. What separates it from an unclaimed spare is that the unclaimed
# one is still sitting in the daemon's scratch directory, while a claimed one has moved to the
# directory you are working in.
_SCRATCH = re.compile(r"/tmp/cc-daemon-")


def _names_plumbing(args: str) -> bool:
    """Whether any argument is one of the modes that means "a service, not a session"."""
    return any(part.lower() in _PLUMBING_SUBCOMMANDS for part in args.split()[1:])


def _now() -> float:
    """The clock, behind one name so a test can move it.

    Several of the rules here are about duration — a run too short to be a task, a green dot
    going stale — and a test that cannot move time can only express them by sleeping.
    """
    return time.time()


@dataclass
class Agent:
    name: str
    pid: int
    cpu: float = 0.0


@dataclass
class State:
    kind: str = "idle"
    agent: str = ""
    detail: str = ""
    # Stable identity for one running -> stopped cycle.  The process can briefly disappear from
    # `ps` while the terminal is redrawn; that must not manufacture a second completion event.
    completion_id: str = ""
    at: float = field(default_factory=_now)

    @property
    def colour(self) -> str:
        return COLOURS.get(self.kind, "blue")


def _working_directory(pid: int) -> str:
    """Where the process thinks it is. Empty when it cannot be read.

    Never a reason to reject on its own: a directory we are not allowed to read must not make a
    real session invisible.
    """
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except Exception:  # noqa: BLE001
        return ""


def is_a_session(pid: int, args: str) -> bool:
    """Whether this process is something a person is talking to, rather than plumbing."""
    if _names_plumbing(args):
        return False
    if _SCRATCH.search(_working_directory(pid)):
        return False
    return True


def _processes() -> list[Agent]:
    """Every coding agent session currently running, by name."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,comm,args"], capture_output=True,
                             text=True, timeout=6).stdout
    except Exception:  # noqa: BLE001
        return []
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        pid, comm, args = int(parts[0]), parts[1].lower(), parts[2].lower()
        for name in AGENTS:
            # The command name is checked before the arguments: every shell that ever *mentioned*
            # claude would otherwise count as claude running, this session's own included.
            named = (comm == name or comm == f"{name}.exe" or comm.startswith(f"{name}-")
                     or args.split(" ", 1)[0].rstrip("/").endswith(f"/{name}"))
            if not named:
                continue
            if is_a_session(pid, args):
                found.append(Agent(name=name, pid=pid))
            break
    return found


def _cpu_jiffies(pid: int) -> Optional[int]:
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().split()
        return int(parts[13]) + int(parts[14])
    except Exception:  # noqa: BLE001
        return None


_seen: dict[int, tuple[float, int]] = {}


def _busy(pid: int) -> float:
    """Share of a core this process has used since the last look."""
    now, jiffies = _now(), _cpu_jiffies(pid)
    if jiffies is None:
        _seen.pop(pid, None)
        return 0.0
    before = _seen.get(pid)
    _seen[pid] = (now, jiffies)
    if before is None:
        return 0.0                     # the first look establishes the baseline
    gap = now - before[0]
    return ((jiffies - before[1]) / gap) if gap > 0.5 else 0.0


# What an agent waiting on a person actually looks like on screen.
#
# The first version of this matched vocabulary — allow, approve, permission, confirm — and went
# red at the mere mention of them. Terminals are full of prose about permissions: commit
# messages, diffs, this project's own source. The dot sat red while nothing was being asked.
#
# A prompt has a shape, and the shape is what is matched now: an affordance for answering, on one
# of the last few lines, on a line short enough to be a prompt rather than a paragraph.
_YES_NO = re.compile(r"(?i)[\(\[]\s*y\s*(?:es)?\s*/\s*n\s*(?:o)?\s*[\)\]]")

# "❯ 1. Yes" / "  2. No, and tell Claude what to do differently" — the numbered choice these
# agents offer. One numbered line is a list; a numbered line offering yes or no is a question.
_NUMBERED_CHOICE = re.compile(r"(?i)^\s*[❯>*→]?\s*\d+\s*[.)]\s*(?:yes|no)\b")

# A direct question, but only when it reads like a prompt rather than a sentence about one.
_DIRECT_QUESTION = re.compile(
    r"(?i)\b(?:do\s+you\s+want|would\s+you\s+like|shall\s+i|may\s+i|proceed|continue)\b"
    r"[^?]{0,60}\?\s*$")

_WAITING_KEYPRESS = re.compile(r"(?i)^\s*press\s+(?:enter|any\s+key|y)\b")

# Past this, a line is prose that happens to contain the words, not something to answer.
PROMPT_LINE_MAX = 90

# Only the live prompt matters, and that is at the bottom.
PROMPT_WITHIN_LINES = 6


# And what it looks like when it has finished and handed the terminal back. Deliberately looser
# than the question patterns: a wrong "done" shows a green dot for ninety seconds, while a wrong
# "asking" claims you are being waited on when you are not.
_FINISHED = re.compile(
    r"""(?ix)
    (?: \btokens?\s+used\b | ^\s*done\b\s*[.!]?\s*$ | \bcompleted\b |
        ^\s*[\w.-]+@[\w.-]+:.*[$\#]\s*$ )      # a shell prompt is back
    """, re.M | re.X)


def classify_words(screen: str) -> str:
    """"asking", "done", or "" from what the terminal is showing."""
    if not screen:
        return ""
    lines = [line.rstrip() for line in screen.splitlines() if line.strip()]
    if not lines:
        return ""

    for line in lines[-PROMPT_WITHIN_LINES:]:
        stripped = line.strip()
        if len(stripped) > PROMPT_LINE_MAX:
            continue                    # a paragraph, not a prompt
        if (_YES_NO.search(stripped)
                or _NUMBERED_CHOICE.match(stripped)
                or _DIRECT_QUESTION.search(stripped)
                or _WAITING_KEYPRESS.match(stripped)):
            return "asking"

    if _FINISHED.search("\n".join(lines[-25:])):
        return "done"
    return ""


# --------------------------------------------------------------------------- per-session state
@dataclass
class Session:
    """One agent process, and what it has been doing since we started watching it."""
    name: str
    pid: int
    kind: str = "idle"
    cpu: float = 0.0
    busy_since: float = 0.0        # 0 when it is not working
    cycles: int = 0                # how many runs we have seen, for a stable completion identity
    stopped_at: float = 0.0
    completion_id: str = ""
    last_seen: float = field(default_factory=_now)

    @property
    def ever_ran(self) -> bool:
        return self.cycles > 0


@dataclass
class Event:
    """One thing that happened to one session, worth saying out loud exactly once."""
    kind: str          # "done" or "asking"
    agent: str
    completion_id: str


_sessions: dict[int, Session] = {}
_events: list[Event] = []
_state = State()

# Most urgent first, and the two transient states come before the steady one. A session waiting
# on an answer is the only thing that needs you; a session that has just finished is news, and
# news it stays for ninety seconds; a session working is a condition, not an event. Ordering
# "running" above "done" meant that while any session worked — which, with three terminals open,
# is most of the time — another one finishing was invisible.
_URGENCY = ("asking", "done", "running", "idle")


def look(read_terminal=None) -> State:
    """One reading. `read_terminal` is called only when it is worth calling."""
    global _state

    now = _now()
    live = {a.pid: a for a in _processes()}

    for pid in list(_sessions):
        if pid in live:
            _sessions[pid].last_seen = now
        elif now - _sessions[pid].last_seen > GONE_AFTER_S:
            _sessions.pop(pid)
            _seen.pop(pid, None)

    # The terminal is read at most once per look, on the first session that has something new to
    # say. Reading it borrows the clipboard, and three sessions stopping together is no reason to
    # borrow it three times. Nothing in Jarvis passes a reader any more — see `_from_transcript`
    # — but the argument stays for callers that want to, and for the tests.
    read_already = False
    words = ""

    for pid, agent in live.items():
        s = _sessions.setdefault(pid, Session(name=agent.name, pid=pid))
        s.cpu = _busy(pid)
        s.last_seen = now

        # The agent's own transcript outranks anything inferred from processor load, because it
        # is a record rather than an inference. Only when there is no transcript does the CPU
        # ladder below get a say.
        if _from_transcript(s, now):
            continue

        if s.cpu > WORKING_ABOVE:
            if not s.busy_since:
                s.busy_since = now
                s.cycles += 1
            s.kind = "running"
            s.stopped_at = 0.0
            continue

        if s.busy_since:
            # It has just stopped. Why needs the words, and those are only read on the change.
            ran_for = now - s.busy_since
            s.busy_since = 0.0
            s.stopped_at = now
            if ran_for < MIN_TASK_S:
                # A twitch, not a task. Nothing finished, so nothing to say about it.
                s.cycles = max(0, s.cycles - 1)
                s.kind = "idle"
                continue
            if not read_already and read_terminal is not None:
                words, read_already = classify_words(read_terminal() or ""), True
            s.kind = words or "done"
            s.completion_id = f"{s.name}:{pid}:{s.cycles}"
            # Recorded per session rather than left for the dot to carry. The dot can only show
            # one thing, so a second session finishing while the first is still green — or while
            # any session is still working — would otherwise never be announced at all.
            _events.append(Event(kind=s.kind, agent=s.name, completion_id=s.completion_id))
            continue

        # Not working, and not working a moment ago either.
        if s.kind == "done" and now - s.stopped_at > DONE_FOR_S:
            s.kind = "idle"             # green means "just finished"; this is only still open
        elif not s.ever_ran:
            # Open, but it has never done anything we saw. That is not a completion, and saying
            # so on the first look is how a restart came to announce work nobody had asked for.
            s.kind = "idle"

    _state = _summarise()
    return _state


def _from_transcript(s: "Session", now: float) -> bool:
    """Set this session's state from what the agent wrote down. True when it could.

    The completion identity is the uuid of the message that ended the turn, so a finished turn is
    announced exactly once and can never be announced twice — however often this is called, and
    whatever the process does between calls. That is the fix for the spam, and it is structural
    rather than bookkeeping.
    """
    cwd = _working_directory(s.pid)
    if not cwd:
        return False
    turn = transcripts.state_for(cwd, now=now)
    if turn is None:
        return False

    if turn.kind == "done":
        if s.completion_id != turn.completion_id:
            # A turn we have not seen end before.
            s.completion_id = turn.completion_id
            s.cycles += 1
            s.stopped_at = now
            _events.append(Event(kind="done", agent=s.name, completion_id=turn.completion_id))
        s.busy_since = 0.0
        # Green means "just finished". After that it is only a window that happens to be open.
        s.kind = "done" if now - s.stopped_at <= DONE_FOR_S else "idle"
        return True

    # Anything that is not a finished turn is a running one. The transcript deliberately does not
    # try to tell "blocked on a permission prompt" from "running a slow tool" — see the note in
    # transcripts.py about why that guess is worse than not making it.
    s.kind = "running"
    s.busy_since = s.busy_since or now
    s.stopped_at = 0.0
    return True


def _summarise() -> State:
    """One state for the dot, from however many sessions are open."""
    if not _sessions:
        return State(kind="idle")
    for kind in _URGENCY:
        here = [s for s in _sessions.values() if s.kind == kind]
        if not here:
            continue
        if kind == "idle":
            break
        # The busiest of the equally urgent, so "running" names the one actually working.
        s = max(here, key=lambda x: x.cpu)
        detail = f"{s.cpu:.0f}% of a core" if kind == "running" else ""
        return State(kind=kind, agent=s.name, detail=detail, completion_id=s.completion_id)
    any_open = max(_sessions.values(), key=lambda x: x.cpu)
    return State(kind="idle", agent=any_open.name, detail="still open, nothing running",
                 completion_id=any_open.completion_id)


def drain_events() -> list[Event]:
    """Everything that has happened since this was last asked. Reading it clears it."""
    global _events
    out, _events = _events, []
    return out


def working_directories(name: str = "") -> list[str]:
    """Where the live agent sessions are working, newest first.

    The transcript for a session is found from its working directory, and nothing else on the
    machine knows that mapping — so it is published here, beside the process list it comes from.
    """
    out: list[str] = []
    for agent in _processes():
        if name and agent.name != name:
            continue
        cwd = _working_directory(agent.pid)
        if cwd and cwd not in out:
            out.append(cwd)
    return out


def sessions() -> list[Session]:
    """Every session being watched, for anything that wants more than one dot's worth."""
    return sorted(_sessions.values(), key=lambda s: (s.name, s.pid))


def current() -> State:
    return _state


def reset() -> None:
    global _state
    _state = State()
    _sessions.clear()
    _seen.clear()
    _events.clear()
