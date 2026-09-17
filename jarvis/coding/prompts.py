"""Turn what was said out loud into the prompt the coding agent actually gets.

Spoken requests are short, and they are short in the way speech is: "ok but now we need to add a
dark mode toggle" leaves out the repository, the framework, what "settings page" refers to, and
every constraint the speaker has in their head and did not say. Typed straight into an agent it
produces confident work on the wrong thing.

So ChatGPT writes the prompt. Not because it knows the codebase — it does not — but because the
account's own custom instructions say how these prompts should be written, and those live where
they can be edited without touching this code. What is sent is only the facts: which agent and
model will receive it, where it will run, and the words that were actually said.

If ChatGPT cannot be reached the spoken words are used unchanged. A prompt that is merely terse
is worth far more than no prompt at all, and this must never be the reason a request does nothing.
"""

from __future__ import annotations

import asyncio
from typing import Optional

# Long enough for a considered rewrite, short enough that nobody waits at the editor wondering
# whether anything is happening.
TIMEOUT_S = 75

_session = None
_lock = asyncio.Lock()


async def _reach_chatgpt():
    """A logged-in ChatGPT session, started once and kept, or None."""
    global _session
    if _session is not None:
        return _session
    try:
        from ..integrations.chatgpt import ChatGPTSession

        session = ChatGPTSession()
        await session.start()
        _session = session
        return _session
    except Exception:  # noqa: BLE001 — no ChatGPT is a reason to send the words as they were
        return None


def brief(said: str, agent: str, model: str, effort: str, workspace: str,
          carrying_on: str = "") -> str:
    """Everything ChatGPT is told. Facts only — the instructions live in the account."""
    lines = [
        f"CODING AGENT: {agent}",
        f"MODEL: {model}",
        f"REASONING EFFORT: {effort}",
        f"WORKSPACE: {workspace}",
        f"WHAT I SAID: {said}",
    ]
    if carrying_on:
        lines.append(f"PREVIOUS AGENT'S HANDOVER NOTES: {carrying_on}")
    return "\n".join(lines)


async def rewrite(said: str, agent: str, model: str, effort: str, workspace: str,
                  carrying_on: str = "") -> tuple[str, str]:
    """(prompt to type, where it came from)."""
    words = " ".join((said or "").split())
    if not words:
        return "", "nothing was said"

    async with _lock:
        session = await _reach_chatgpt()
    if session is None:
        return words, "ChatGPT was not reachable, so your words went as they were"

    try:
        answer = await asyncio.wait_for(
            session.ask_fresh(brief(words, agent, model, effort, workspace, carrying_on)),
            timeout=TIMEOUT_S + 15)
    except Exception:  # noqa: BLE001
        return words, "ChatGPT did not answer, so your words went as they were"

    prompt = _just_the_prompt(answer)
    if not prompt:
        return words, "ChatGPT returned nothing usable, so your words went as they were"
    return prompt, "written by ChatGPT"


def _just_the_prompt(answer: str) -> Optional[str]:
    """The prompt out of ChatGPT's reply, without the scaffolding it sometimes adds.

    A fenced block is taken as the whole answer when there is one, because a reply that explains
    itself and then gives the prompt in a fence means the fence. Otherwise the reply is used as
    it stands, minus any leading label.
    """
    import re

    text = (answer or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:[a-zA-Z]*\n)?(?P<body>.+?)```", text, re.S)
    if fenced:
        text = fenced.group("body").strip()
    # The label a chat model puts in front of an answer, in the several shapes it uses. Left in,
    # it becomes the first line the coding agent reads and is taken as part of the instruction.
    text = re.sub(
        r"(?i)^(?:"
        r"(?:here(?:'?s| is)\s+)?(?:the\s+|your\s+)?(?:rewritten\s+|final\s+)?prompt"
        r"|use\s+this|try\s+this"
        r")\s*[:\-\u2014]\s*", "", text).strip()
    # Typed into a terminal as one line; a newline would submit half of it.
    return " ".join(text.split()) or None
