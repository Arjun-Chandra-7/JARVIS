"""Ask the agent whether its turn is over, instead of guessing from processor load.

The dot and the announcements were driven by CPU: above a threshold meant working, below it meant
finished. That reading is wrong in a way no threshold fixes, because a coding agent's turn is not
one continuous burst of work. It thinks, calls a tool, waits on an API, writes a file, thinks
again — and every dip between those is indistinguishable from the end of the turn. One prompt
produces a handful of "finished" moments, which is what "it spams me even when it's working"
describes.

Measured on this machine: a session actively mid-turn held 5.0-17.5% of a core over ninety
seconds, against a threshold of 5.0. Its *floor* was the threshold. It happened not to misfire in
that window; it was one sample away from doing so, and which side of the line a sample lands on
is not a thing worth basing an announcement on.

The agents already write down what they are doing
-------------------------------------------------
Claude Code appends every message to a JSONL transcript under ~/.claude/projects/<slug>/, live,
as the turn proceeds. The last assistant message carries a `stop_reason`, and it is exactly the
thing being guessed at:

    tool_use    it stopped to run something — mid-turn, still working
    end_turn    it has finished and is waiting for you

That is not a heuristic. It is the agent's own account of its state, and it is free to read.

Two problems solved, not one
----------------------------
The old path also had to read the terminal to tell "waiting for permission" from "done", and it
did that by opening the command palette, running Terminal: Select All, copying, and pressing
escape — while you were typing. That is the other complaint, and it disappears here: the
transcript says everything the terminal was being interrogated for.

Announcing exactly once, for ever
---------------------------------
Every message has a uuid. A completion is keyed on the uuid of the `end_turn` message that
produced it, so a turn can be announced exactly once and can never be announced twice, however
many times the file is read, however the process behaves, and across restarts of the watcher.

What is read
------------
`role`, `stop_reason`, `uuid`, `timestamp`. Never message content — Jarvis does not need to know
what you asked an agent in order to know that it has stopped.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

PROJECTS = os.path.expanduser(os.environ.get("JARVIS_CLAUDE_PROJECTS", "~/.claude/projects"))

# Transcripts grow to tens of megabytes over a long session and are appended to constantly. Only
# the end is ever interesting, and reading the whole file every four seconds would be the most
# expensive thing this program does.
TAIL_BYTES = 512 * 1024

# A turn that stopped on tool_use and then went quiet is blocked on something — nearly always a
# permission prompt, because that is the only thing that stops an agent mid-tool. Long enough not
# to fire during an ordinary slow tool call.
ASKING_AFTER_S = 45.0

# A transcript nobody has written to in this long belongs to a session that is over, whatever its
# last message said. Without this, every stale project directory on the disk reports a finished
# turn for ever.
FORGET_AFTER_S = 6 * 3600


@dataclass(frozen=True)
class Turn:
    """What an agent's own transcript says it is doing."""

    kind: str                  # working | asking | done
    completion_id: str         # the uuid of the message that ended the turn, when it has ended
    at: float                  # when the transcript was last written


def slug_for(cwd: str) -> str:
    """The project directory name Claude Code derives from a working directory.

    Every one of `/`, `.` and `_` becomes a dash — worked out by comparing real directory names
    against the paths that produced them, because two of the three are easy to miss:
    `/home/xor_sensei/…/.claude/…` becomes `-home-xor-sensei-…--claude-…`.
    """
    return re.sub(r"[/._]", "-", cwd)


def transcript_for(cwd: str) -> Optional[str]:
    """The live transcript for a session running in `cwd`, if there is one.

    Subagent transcripts live in a subdirectory and are deliberately not considered: a subagent
    finishing is not the turn finishing, and announcing it would be the same spam by another
    route.
    """
    directory = os.path.join(PROJECTS, slug_for(cwd))
    files = glob.glob(os.path.join(directory, "*.jsonl"))
    if not files:
        return None
    newest = max(files, key=_mtime)
    return newest if time.time() - _mtime(newest) < FORGET_AFTER_S else None


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _tail_records(path: str, keep: int = 300) -> list[dict]:
    """The last few records, without reading the whole file."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                fh.readline()          # the seek almost certainly landed mid-line; drop it
            blob = fh.read().decode("utf-8", "ignore")
    except OSError:
        return []

    out: list[dict] = []
    for line in blob.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue               # a half-written final line is normal on a live file
    return out[-keep:]


def read(path: str, now: Optional[float] = None) -> Optional[Turn]:
    """What the transcript says, or None when it says nothing useful."""
    moment = now if now is not None else time.time()
    written = _mtime(path)
    for record in reversed(_tail_records(path)):
        message = record.get("message") or {}
        if message.get("role") != "assistant":
            continue
        reason = message.get("stop_reason")
        if not reason:
            continue               # streaming, or a record without one; keep looking back
        uuid = str(record.get("uuid") or "")
        if reason == "end_turn":
            return Turn(kind="done", completion_id=uuid, at=written)
        # Stopped to use a tool. Still working — unless nothing has been written since, in which
        # case it is waiting on somebody, and that somebody is you.
        quiet_for = moment - written
        kind = "asking" if quiet_for > ASKING_AFTER_S else "working"
        return Turn(kind=kind, completion_id="", at=written)
    return None


def state_for(cwd: str, now: Optional[float] = None) -> Optional[Turn]:
    """What the agent working in `cwd` is doing, from its own record of it."""
    path = transcript_for(cwd)
    return read(path, now=now) if path else None
