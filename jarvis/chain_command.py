"""«do this, then do that» — two or three capabilities in one sentence.

    "generate an image of Iron Man brushing, then find a free whiteboard site and draw this"
    "open YouTube, then search for lofi"
    "make a picture of a dragon and then draw it on a whiteboard"

Each part is answered by the same deterministic handlers that answer it on its own, in the same
order, so nothing here needs to know what a whiteboard is or how a picture gets made. What this
adds is the joining: the parts run in sequence, later parts see what earlier ones left behind —
the picture that was just generated, the site that was just opened — and the whole thing stops at
the first part that fails rather than carrying on and reporting success.

Why not leave it to the planner in task_runner: that plans a request into browser steps, and its
vocabulary is open_site, click_link, type_text. A picture being generated is not a browser step.
These are whole capabilities, and the thing that knows how to run a capability is the layer that
already routes to them.

Deliberately conservative. A sentence is only treated as a chain when every part is claimed by a
handler. "Open the door then tell me a joke" has no handler for either part, so it goes to the
model untouched, which is where it belongs.
"""

from __future__ import annotations

import re
from typing import Optional

# What separates the parts. "then" is the reliable one; a bare "and" is not — "find a free
# whiteboard site and draw me the Mona Lisa" is one request to one handler, and splitting it
# would break the thing that already works.
_SPLIT = re.compile(
    r"""(?ix)
    \s*(?:,\s*)?
    (?: and\s+then | ,\s*then | \bthen\b | after\s+that | and\s+after\s+that )
    \s+""")

# Deliberately not split on a bare "and". "Find a free whiteboard site and draw me the Mona Lisa"
# is one request that one handler already answers as a unit — it finds the surface and draws on
# it — and splitting there would take a working sentence apart into a half that opens a site and
# a half that draws on whatever happened to be in front of it.

MAX_PARTS = 3

# A part that is only a courtesy — "for me", "please" — is not a step.
_EMPTY = re.compile(r"(?ix)^(?:please|for\s+me|thanks|thank\s+you|ok(?:ay)?|now)?$")


def split(text: str) -> list[str]:
    """The parts of a chained request, or a single-item list when it is not chained."""
    said = (text or "").strip().rstrip(".!?")
    parts = [p.strip(" ,.;:") for p in _SPLIT.split(said)]
    return [p for p in parts if p and not _EMPTY.match(p)]


def _reads_as_a_failure(reply: str) -> bool:
    """Whether a handler's answer says it did not manage it.

    The handlers report in sentences rather than status codes, so this reads the sentences. It
    errs towards carrying on: a part wrongly called a failure stops a chain that was working,
    which is worse than a part wrongly called a success, where the next part fails honestly.
    """
    said = (reply or "").strip().lower()
    return bool(re.match(
        r"(?:i\s+(?:couldn'?t|could\s+not|can'?t|cannot|didn'?t)|there\s+is\s+no|"
        r"there\s+was\s+no|no\s+\w+\s+(?:found|matches)|sorry)", said))


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not a chain' — one part, or a part nobody claims."""
    parts = split(text)
    if len(parts) < 2 or len(parts) > MAX_PARTS:
        return None

    from .commands import deterministic_handlers

    # Everything except this, or a chain would find itself inside each of its own parts.
    handlers = [(name, fn) for name, fn in deterministic_handlers() if name != "chain"]

    # Claimed first, all of them, before anything runs. A chain whose second half nobody
    # understands must not leave the first half already carried out.
    claims: list[tuple[str, object]] = []
    for part in parts:
        for name, fn in handlers:
            if _claims(name, part, config):
                claims.append((part, fn))
                break
        else:
            return None

    done: list[str] = []
    for part, fn in claims:
        reply = await fn(part, config)
        if reply is None:
            # Claimed a moment ago and not now: the world moved, or the claim was optimistic.
            return _summarise(done, f"I couldn't work out how to {part}.") if done else None
        done.append(reply.strip())
        if _reads_as_a_failure(reply):
            return _summarise(done[:-1], done[-1])
    return _summarise(done, "")


def _claims(name: str, part: str, config) -> bool:
    """Whether this handler would take this part, asked without running anything.

    Each handler already has a parser that decides exactly this, and asking the parser is the only
    honest way to know — running the handler to find out would carry out half a chain that the
    rest of the sentence might not support.
    """
    try:
        if name == "imagine":
            from .imagine_command import parse
            return parse(part) is not None
        if name == "draw":
            from .draw_command import parse
            return parse(part) is not None
        if name == "find_site":
            from .find_site import parse, parse_find_and_draw
            return parse(part) is not None or parse_find_and_draw(part) is not None
        if name == "open":
            from .open_command import parse
            return parse(part) is not None
        if name == "task":
            from .task_runner import recipe
            return bool(recipe(part))
        if name == "system":
            from .system_command import parse
            return parse(part) is not None
    except Exception:  # noqa: BLE001 — a parser that throws has not claimed anything
        return False
    return False


def _summarise(done: list[str], failure: str) -> str:
    """One reply for the whole chain, in the order it happened."""
    parts = [d for d in done if d]
    if failure:
        parts.append(failure)
    if not parts:
        return "Nothing to do, sir."
    # Each part already speaks in whole sentences; joining them with a space is enough, and
    # rewriting them would risk losing the specific reason a part gave for failing.
    return " ".join(p if p.endswith((".", "!", "?")) else p + "." for p in parts)
