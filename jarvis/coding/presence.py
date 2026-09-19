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
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

# What each state means to the eye. The dot on the pill takes these directly.
COLOURS = {
    "idle": "blue",         # nothing open
    "running": "orange",    # working
    "asking": "red",        # wants an answer from you
    "done": "green",        # finished, still open
}

AGENTS = ("claude", "codex", "agy")

# Above this share of a core, over one sampling gap, the agent is doing something. Well below a
# busy process and well above the idle twitching of a program waiting on a read.
WORKING_ABOVE = 3.0

# How long a finished agent stays green before it is just an open window again.
DONE_FOR_S = 90.0


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
    at: float = field(default_factory=time.time)

    @property
    def colour(self) -> str:
        return COLOURS.get(self.kind, "blue")


def _processes() -> list[Agent]:
    """Every coding agent currently running, by name."""
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
            if comm == name or comm == f"{name}.exe" or comm.startswith(f"{name}-"):
                found.append(Agent(name=name, pid=pid))
                break
            if args.split(" ", 1)[0].rstrip("/").endswith(f"/{name}"):
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
    now, jiffies = time.time(), _cpu_jiffies(pid)
    if jiffies is None:
        _seen.pop(pid, None)
        return 0.0
    before = _seen.get(pid)
    _seen[pid] = (now, jiffies)
    if before is None:
        return 0.0                     # the first look establishes the baseline
    gap = now - before[0]
    return ((jiffies - before[1]) / gap) if gap > 0.5 else 0.0


# What an agent looks like when it is waiting on a person rather than on a model.
_ASKING = re.compile(
    r"""(?ix)
    (?: do\s+you\s+want\s+to | would\s+you\s+like\s+to | shall\s+i |
        allow | approve | permission | confirm |
        \(\s*y\s*/\s*n\s*\) | \[\s*y\s*/\s*n\s*\] |
        press\s+enter\s+to | ❯\s*\d\.\s | \b1\.\s*yes\b )
    """)

# And what it looks like when it has finished and handed the terminal back.
_FINISHED = re.compile(
    r"""(?ix)
    (?: \btokens?\s+used\b | \bdone\b\s*[.!]? \s*$ | \bcompleted\b |
        ^\s*[\w.-]+@[\w.-]+:.*[$#]\s*$ )      # a shell prompt is back
    """, re.M)


def classify_words(screen: str) -> str:
    """"asking", "done", or "" from what the terminal is showing."""
    if not screen:
        return ""
    tail = "\n".join(screen.splitlines()[-25:])
    if _ASKING.search(tail):
        return "asking"
    if _FINISHED.search(tail):
        return "done"
    return ""


_state = State()
_stopped_at: float = 0.0


def look(read_terminal=None) -> State:
    """One reading. `read_terminal` is called only when it is worth calling."""
    global _state, _stopped_at

    agents = _processes()
    if not agents:
        _stopped_at = 0.0
        _state = State(kind="idle")
        return _state

    # Sampled once each. Asking twice in the same breath resets the baseline and the second
    # reading is always zero, because no time has passed between them — which made every agent
    # look idle for ever.
    for agent in agents:
        agent.cpu = _busy(agent.pid)
    busiest = max(agents, key=lambda a: a.cpu)

    if busiest.cpu > WORKING_ABOVE:
        _stopped_at = 0.0
        _state = State(kind="running", agent=busiest.name,
                       detail=f"{busiest.cpu:.0f}% of a core")
        return _state

    # It has stopped. Why it stopped needs the words, and those are only read on the change.
    now = time.time()
    if _state.kind == "running" or _stopped_at == 0.0:
        _stopped_at = now
        words = classify_words(read_terminal() if read_terminal else "")
        _state = State(kind=words or "done", agent=busiest.name)
        return _state

    # Already known to have stopped: stay as we were, until green goes stale.
    if _state.kind == "done" and now - _stopped_at > DONE_FOR_S:
        _state = State(kind="idle", agent=busiest.name, detail="still open, nothing running")
    return _state


def current() -> State:
    return _state


def reset() -> None:
    global _state, _stopped_at
    _state, _stopped_at = State(), 0.0
    _seen.clear()
