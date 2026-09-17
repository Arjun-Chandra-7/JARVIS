"""«ok, but now we need to add X» — said at the editor, carried out by a coding agent.

The agent is started in the editor's integrated terminal and the prompt is typed into it, rather
than run headlessly in the background. That is slower and it is the point: you can see which
agent was chosen, what it was asked, and what it answered, and you can take the keyboard back
mid-conversation and carry on yourself.

Deliberately hard to trigger. "We need to add a test for that" said in the kitchen is a remark,
and the same words said at the editor are a request, so this only takes the turn when there is
reason to believe it is one: an agent is already in conversation, or one was named, or the editor
is the window in front of you.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

from .coding import quota, roster, session, vscode

# How the next piece of work gets asked for, once you are already working.
_ASK = re.compile(
    r"""(?ix)^(?:ok(?:ay)?|right|so|and|but|alright)?[,\s]*
        (?:
            (?:but\s+)?now\s+(?:we|i|you)\s+(?:need\s+to|have\s+to|should|must|want\s+to)\s+ |
            (?:we|i)\s+(?:need\s+to|have\s+to|should|want\s+to)\s+ |
            (?:let'?s|lets)\s+ |
            (?:can\s+you|could\s+you|please)\s+ |
            (?:tell|ask|get)\s+(?:claude|codex|antigravity|agy|gemini)\s+to\s+
        )
        (?P<work>.{4,400})$""")

# Said plainly to an agent that is already in conversation: "add a dark mode toggle".
_IMPERATIVE = re.compile(
    r"""(?ix)^(?:
        add | write | create | make | build | implement | fix | refactor | rename | delete |
        remove | move | update | change | test | debug | explain | review | optimi[sz]e |
        install | run | commit | revert | undo
    )\b.{3,400}$""")


# "We should add a retry to the upload" and "we should go out for dinner" have the same shape,
# and only one of them is for a coding agent. The work itself has to look like work on software.
_CODE_WORK = re.compile(
    r"""(?ix)
    ^(?: add | write | create | make | build | implement | fix | refactor | rename | delete |
         remove | move | update | change | test | debug | explain | review | optimi[sz]e |
         install | migrate | deploy | commit | revert | undo | wire | hook | expose | handle )\b
    |
    \b(?: function | method | class | module | file | folder | directory | component | endpoint |
          route | api | schema | database | table | query | migration | test | tests | bug |
          error | exception | crash | build | deploy | commit | branch | merge | repo |
          css | html | json | yaml | config | server | client | frontend | backend | ui |
          button | form | page | script | package | dependency | import | type | interface )\b
    """)


def looks_like_software(work: str) -> bool:
    return bool(_CODE_WORK.search(work or ""))


def parse(text: str) -> Optional[str]:
    """The work to hand to a coding agent, or None."""
    said = (text or "").strip().rstrip()
    if not said:
        return None
    match = _ASK.match(said)
    if match:
        work = match.group("work").strip(" ,.")
        # An agent named outright is a clear instruction whatever the words after it.
        if work and (looks_like_software(work) or roster.named_in(said)):
            return work
        return None
    # A bare imperative only counts when an agent is mid-conversation; otherwise "open netflix"
    # and "delete all my screenshots" would both look like coding work.
    if session.current() and _IMPERATIVE.match(said):
        return said.strip(" ,.")
    return None


def _should_take_it(text: str) -> bool:
    """Whether this turn is really about the code in front of you."""
    if session.current():
        return True                       # a conversation is already running
    if roster.named_in(text):
        return True                       # an agent was asked for by name
    return vscode.window() is not None and vscode.active_window() == vscode.window()


def workspace() -> str:
    """Where the agent should work. The editor's own folder, when it can be read from the title."""
    import subprocess

    wid = vscode.window()
    if wid:
        try:
            title = subprocess.run(["xdotool", "getwindowname", wid],
                                   capture_output=True, text=True, timeout=4).stdout
            # "file.py - ProjectName - Visual Studio Code"
            parts = [p.strip() for p in title.split(" - ") if p.strip()]
            if len(parts) >= 2:
                guess = Path.home() / "Madara" / "Dev" / parts[-2]
                if guess.is_dir():
                    return str(guess)
        except Exception:  # noqa: BLE001
            pass
    return str(Path.cwd())


async def run(work: str, text: str) -> str:
    live = session.current()

    if live is None:
        prefer = roster.named_in(text)
        chosen = roster.pick(work, quota.usable(), prefer)
        if chosen is None:
            skipped = [quota.why_not(n) for n in roster.PREFERENCE]
            return "None of the coding agents are available, sir — " + \
                   ", ".join(s for s in skipped if s) + "."
        agent, model, effort = chosen

        ready, switching = await asyncio.to_thread(vscode.ensure_front)
        if not ready:
            blocking = await asyncio.to_thread(vscode.blocked_by)
            if blocking:
                return (f"I can't get to the editor, sir — {blocking} is in front of it. "
                        f"Bring VS Code up and ask me again.")
            return "I can't find VS Code open, sir."

        if not await asyncio.to_thread(vscode.new_terminal):
            return "I couldn't open a terminal in the editor, sir."

        where = workspace()
        command = session.launch_command(agent.name, model, effort, where)
        if not await asyncio.to_thread(vscode.type_line, command):
            return "I couldn't type into the editor's terminal, sir."
        await asyncio.sleep(4.0)          # the agent has to come up before it will take a prompt
        live = session.begin(agent, model, effort, where)
        opening = f"{switching + ' ' if switching else ''}Started {agent.spoken} on {model} at {effort} effort. "
    else:
        ready, switching = await asyncio.to_thread(vscode.ensure_front)
        if not ready:
            return "I can't get to the editor, sir."
        opening = f"{switching + ' ' if switching else ''}Passed it to {live.spoken}. "

    if not await asyncio.to_thread(vscode.send_prompt, work):
        return opening + "But I couldn't type the prompt."
    live.touch()
    return opening + f"Asked it to {work[:90]}."


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not a coding request'."""
    work = parse(text)
    if work is None or not _should_take_it(text):
        return None
    try:
        return await run(work, text)
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't hand that to a coding agent — {type(exc).__name__}: {exc}"
