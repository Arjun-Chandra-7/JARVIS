"""The coding agent currently live in the editor's terminal.

The point of keeping this is the follow-up. "Ok, but now we need to add X" said thirty seconds
after the first request is the same conversation, and starting a second agent would throw away
everything the first one had learned about the codebase. So the first request opens a terminal
and starts an agent; every request after that is typed into the same one.

The model and effort are chosen once, when the agent starts, and never revised. An agent whose
model changes mid-conversation contradicts its own earlier answers, and the person watching has
no way to tell why.
"""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass, field
from typing import Optional

from . import roster

# A conversation that has been idle this long is over; the next request starts a fresh agent.
STALE_AFTER_S = 90 * 60


@dataclass
class Live:
    agent: str
    model: str
    effort: str
    workspace: str
    started: float = field(default_factory=time.time)
    last: float = field(default_factory=time.time)
    turns: int = 0

    def fresh(self) -> bool:
        return (time.time() - self.last) < STALE_AFTER_S

    def touch(self) -> None:
        self.last = time.time()
        self.turns += 1

    @property
    def spoken(self) -> str:
        return roster.BY_NAME[self.agent].spoken


_live: Optional[Live] = None


def current() -> Optional[Live]:
    """The agent still in conversation, or None."""
    global _live
    if _live is not None and not _live.fresh():
        _live = None
    return _live


def begin(agent: roster.Agent, model: str, effort: str, workspace: str) -> Live:
    global _live
    _live = Live(agent=agent.name, model=model, effort=effort, workspace=workspace)
    return _live


def end() -> None:
    global _live
    _live = None


def launch_command(agent: str, model: str, effort: str, workspace: str) -> str:
    """The command line to type, exactly as a person would type it.

    Interactive, not one-shot: `claude --print` answers once and exits, which makes every
    follow-up a new conversation with no memory of the last. The whole point here is that the
    session stays open in front of you.
    """
    where = shlex.quote(workspace)
    if agent == "claude":
        return f"cd {where} && claude --model {model} --effort {effort}"
    if agent == "codex":
        return (f"cd {where} && codex -c model_reasoning_effort=\"{effort}\" -m {model}")
    if agent == "agy":
        return f"cd {where} && agy --model {model} --effort {effort} --mode accept-edits"
    raise ValueError(f"unknown coding agent: {agent}")
