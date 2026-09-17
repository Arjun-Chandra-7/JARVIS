"""Wind an agent up before it runs out, and start the next one where it left off.

An agent that hits its limit mid-task stops in the middle of a sentence and leaves the work in
whatever state it had reached — half-applied edits, a plan it never explained, and nothing written
down about what it had understood. Everything it learned about the codebase over an hour is gone.

So it is retired deliberately instead, while it still has enough left to answer one more question.
That question is "what have you done and what is left", and the answer is the only thing worth
carrying across: the next agent is a different model with a different way of working, and handing
it a transcript would be worse than handing it a summary written by the one that did the work.

The threshold is 5%, which is not a lot — one more exchange — and that is the intent. Retiring an
agent at 30% to be safe wastes most of what was paid for.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from . import prompts, roster, session, terminal, usage, vscode

# Asked of the agent that is going. Deliberately blunt: it has very little left to answer with.
SUMMARY_REQUEST = (
    "You are almost out of quota and another agent will take over. In under 200 words: what have "
    "you changed so far, what is still unfinished, and what should the next agent know that is "
    "not obvious from the code?"
)

# How the agents are asked to leave. Typed rather than killed, so the session closes cleanly and
# anything it wanted to write on the way out gets written.
GOODBYE = {"claude": "/exit", "codex": "/exit", "agy": "/exit"}

SUMMARY_WAIT_S = 25.0


@dataclass
class Handover:
    from_agent: str
    to_agent: str
    to_model: str
    to_effort: str
    summary: str
    prompt: str


def next_agent(after: str, work: str) -> Optional[tuple]:
    """The agent to take over, skipping the one that is finishing."""
    from . import quota

    available = quota.usable()
    available[after] = False        # it is going; it cannot also be the one taking over
    return roster.pick(work, available)


async def ask_for_summary() -> str:
    """Ask the finishing agent what it did, and read the answer off the terminal."""
    ready, _switching = await asyncio.to_thread(vscode.ensure_front)
    if not ready:
        return ""
    if not await asyncio.to_thread(vscode.send_prompt, SUMMARY_REQUEST):
        return ""
    await asyncio.sleep(SUMMARY_WAIT_S)
    said = await asyncio.to_thread(terminal.tail, 80)
    return (said or "").strip()


async def say_goodbye(agent: str) -> None:
    """Close the session down rather than leaving it holding the terminal."""
    await asyncio.to_thread(vscode.type_line, GOODBYE.get(agent, "/exit"))
    await asyncio.sleep(1.5)


async def carry_on(work: str, live: session.Live) -> Optional[Handover]:
    """Retire the agent that is nearly out and hand its work to the next one.

    Returns None when there is nobody to hand to — in which case the current agent keeps going,
    because a nearly-empty agent is still better than no agent.
    """
    following = next_agent(live.agent, work)
    if following is None:
        return None
    agent, model, effort = following

    summary = await ask_for_summary()
    await say_goodbye(live.agent)

    # The next agent is told what happened and by whom: models differ, and knowing that the work
    # so far came from a different one explains any style it is about to meet in the diff.
    prompt, _source = await prompts.rewrite(
        work, agent.name, model, effort, live.workspace,
        carrying_on=(f"{roster.BY_NAME[live.agent].spoken} ({live.model}) ran out of quota "
                     f"part-way through and left these notes: {summary[:1500]}"))

    return Handover(from_agent=live.agent, to_agent=agent.name, to_model=model,
                    to_effort=effort, summary=summary, prompt=prompt)


def nearly_out(agent: str) -> Optional[float]:
    """Percent left when the agent is close enough to the end to wind up, else None."""
    left = usage.left_for(agent, in_session=True)
    return left.percent if left.nearly_out() else None
