"""Which coding agent, which model, and how hard it should think.

Three agents are installed, and they are not interchangeable — they differ in what they cost,
what they are good at, and which models they can be asked for. The order of preference is fixed
(Claude, then Codex, then Antigravity) and only budget moves it: an agent with little left is
skipped rather than started and stopped halfway through a job.

The model follows from the effort, not the other way round, because each agent only offers
certain models at certain efforts:

    claude   low, medium  ->  opus 5          high  ->  sonnet
    codex    low, medium  ->  gpt-5.6-sol     high  ->  gpt-5.6-terra
    agy      high only    ->  gemini 3.8

Chosen once, at the start of a piece of work, and then left alone. Changing model or effort
mid-conversation makes the agent's later answers disagree with its earlier ones, and the person
watching has no idea why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

LOW, MEDIUM, HIGH = "low", "medium", "high"


@dataclass(frozen=True)
class Agent:
    name: str            # the command, and what the user calls it
    spoken: str          # what Jarvis calls it out loud
    models: dict         # effort -> model name
    efforts: tuple       # the efforts this agent actually offers


AGENTS = (
    Agent(
        name="claude",
        spoken="Claude",
        models={LOW: "opus", MEDIUM: "opus", HIGH: "sonnet"},
        efforts=(LOW, MEDIUM, HIGH),
    ),
    Agent(
        name="codex",
        spoken="Codex",
        models={LOW: "gpt-5.6-sol", MEDIUM: "gpt-5.6-sol", HIGH: "gpt-5.6-terra"},
        efforts=(LOW, MEDIUM, HIGH),
    ),
    Agent(
        # Antigravity offers one model and one effort, so there is nothing to choose.
        name="agy",
        spoken="Antigravity",
        models={HIGH: "gemini-3.8"},
        efforts=(HIGH,),
    ),
)

BY_NAME = {a.name: a for a in AGENTS}
PREFERENCE = ("claude", "codex", "agy")


# --------------------------------------------------------------------------- how hard to think
# Read from the request, because the person asking already said how big it is. "Add a button" and
# "work out why the websocket drops under load" are not the same job and should not cost the same.
_HARD = re.compile(
    r"""(?ix)\b(
        why | debug | diagnose | investigate | root\s+cause | race | deadlock | leak |
        refactor | redesign | architect | migrate | rewrite | port |
        performance | optimi[sz]e | profil\w* | concurren\w* | thread\w* | async |
        security | vulnerab\w* | auth\w* | crypto |
        failing | flaky | intermittent | regression | broken\s+since
    )\b""")

_EASY = re.compile(
    r"""(?ix)\b(
        rename | typo | comment | format | lint | import | spacing | indent |
        bump | version | changelog | readme | docstring |
        add\s+a\s+(?:log|print|comment|test\s+case) | one[- ]liner | small\s+fix
    )\b""")


def effort_for(request: str) -> str:
    """How hard the agent should think about this, from what was asked."""
    said = (request or "").strip()
    if not said:
        return MEDIUM
    if _HARD.search(said):
        return HIGH
    if _EASY.search(said):
        return LOW
    # Long requests tend to carry several requirements; short ones rarely do.
    return HIGH if len(said.split()) > 45 else MEDIUM


def pick(request: str, usable: dict[str, bool],
         prefer: Optional[str] = None) -> Optional[tuple[Agent, str, str]]:
    """(agent, model, effort), or None when nothing has budget left.

    `usable` says which agents have enough left to be worth starting. A named preference wins if
    it is usable, because being asked for Codex by name and given Claude is infuriating.
    """
    wanted = effort_for(request)

    order = list(PREFERENCE)
    if prefer and prefer in BY_NAME:
        order = [prefer] + [n for n in order if n != prefer]

    for name in order:
        if not usable.get(name):
            continue
        agent = BY_NAME[name]
        # An agent that cannot think as hard as the job needs still gets the job if it is next in
        # line — the alternative is not doing the work — but at the hardest setting it has.
        effort = wanted if wanted in agent.efforts else agent.efforts[-1]
        return agent, agent.models[effort], effort
    return None


def named_in(text: str) -> Optional[str]:
    """An agent the request asked for by name, or None."""
    said = (text or "").lower()
    for name, spoken in (("claude", "claude"), ("codex", "codex"),
                         ("agy", "antigravity"), ("agy", "agy"), ("agy", "gemini")):
        if re.search(rf"\b{re.escape(spoken)}\b", said):
            return name
    return None
