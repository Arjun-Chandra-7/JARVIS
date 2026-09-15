"""«find a site that does X» — by opening candidates and checking, not by believing the blurb.

A search result's title says what a page claims to be. Asking a model which of ten titles is a
usable free whiteboard is asking it to guess, and it will answer confidently either way. Opening
each one and looking for a drawing surface is slower by a few seconds and is not a guess.

The check is the whole point, so it is a parameter: whatever "works" means for the thing being
looked for, that is what gets verified before Jarvis says it found something.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

MAX_TRIED = 4


@dataclass
class Found:
    url: str
    host: str
    title: str
    tried: list[str]


async def _has_canvas() -> bool:
    from .integrations import browser

    box = await browser.canvas_box()
    # A favicon or a decorative sparkline is a canvas too; a drawing surface is big.
    return bool(box and box["w"] >= 300 and box["h"] >= 200)


async def _loads_at_all() -> bool:
    from .integrations import browser

    page = await browser.current_page()
    return bool((page.get("url") or "").startswith("http"))


CHECKS: dict[str, Callable[[], Awaitable[bool]]] = {
    "whiteboard": _has_canvas,
    "canvas": _has_canvas,
    "drawing": _has_canvas,
    "draw": _has_canvas,
    "sketch": _has_canvas,
    "paint": _has_canvas,
    "": _loads_at_all,
}


def check_for(purpose: str) -> Callable[[], Awaitable[bool]]:
    low = (purpose or "").lower()
    for word, check in CHECKS.items():
        if word and word in low:
            return check
    return CHECKS[""]


async def find(purpose: str, query: Optional[str] = None,
               settle_s: float = 3.0) -> Optional[Found]:
    """Open search results for `purpose` until one passes its check. None when none does."""
    from .integrations import browser, websearch

    if not browser.control_ready():
        return None

    hits = await asyncio.to_thread(websearch.results, query or purpose, 8)
    if not hits:
        return None

    check = check_for(purpose)
    tried: list[str] = []
    for hit in hits[:MAX_TRIED]:
        tried.append(hit["host"])
        opened = await browser.open_site(hit["url"])
        if not opened.get("ok"):
            continue
        await asyncio.sleep(settle_s)     # a single-page app draws its surface after load
        try:
            if await check():
                return Found(url=hit["url"], host=hit["host"], title=hit["title"], tried=tried)
        except Exception:  # noqa: BLE001 - a broken candidate is just a candidate that failed
            continue
    return None


_ASK = re.compile(
    r"""^(?:please\s+)?
        (?:find|get|open|look\s+for)\s+
        (?:me\s+)?(?:an?\s+|the\s+)?
        (?:(?:online|free|good)\s+)*
        (?:(?:site|website|page|tool)\s+(?:where\s+(?:i|you|we)\s+can\s+\w+\s+|(?:to|for)\s+)?)?
        (?P<purpose>.+?)
        (?:\s+(?:for\s+free|free|online))?$""",
    re.IGNORECASE | re.VERBOSE,
)


def parse(text: str) -> Optional[str]:
    """What kind of site is wanted, or None when this is not that sort of request."""
    said = (text or "").strip().rstrip(".!?")
    if not re.search(r"\b(?:site|website|page|tool)\b", said, re.IGNORECASE):
        return None                       # "find my keys" is not a request for a website
    match = _ASK.match(said)
    if not match:
        return None
    purpose = match.group("purpose").strip()
    # Whatever survived from the wrapper words: "a whiteboard site" is a request for a whiteboard.
    purpose = re.sub(r"^(?:an?|the)\s+", "", purpose, flags=re.IGNORECASE)
    purpose = re.sub(r"\s+(?:site|website|page|tool)$", "", purpose, flags=re.IGNORECASE)
    purpose = re.sub(r"^(?:to|for)\s+", "", purpose, flags=re.IGNORECASE).strip()
    return purpose or None


# "find a free whiteboard and draw me the Mona Lisa" — the two halves in one sentence, which is
# how anyone would actually ask for it. Split on the joining verb rather than trying to write one
# expression that covers every way of phrasing the first half: the real request was "find a online
# site where u can get a whiteboard for free, and draw me the mona lisa", and no single pattern
# was going to survive "u" for "you" and an article in the middle.
_JOIN = re.compile(r"[,;]?\s+(?:and\s+)?(?:then\s+)?(?:draw|sketch|paint)\s+", re.IGNORECASE)
_SURFACE = re.compile(r"\b(whiteboard|canvas|drawing\s+board|sketch\s*pad)\b", re.IGNORECASE)
_LOOKING = re.compile(r"^(?:please\s+)?(?:find|get|open|look\s+for)\b", re.IGNORECASE)
_LEAD = re.compile(r"^(?:me\s+)?(?:an|a|the)\b\s*", re.IGNORECASE)
# "an" before "a", and a word boundary after: without it "an owl" lost its first letter.
_OF = re.compile(r"^(?:picture|image|photo|drawing|sketch)\s+of\s+(?:(?:an|a|the)\b\s*)?",
                 re.IGNORECASE)


def parse_find_and_draw(text: str) -> Optional[tuple[str, str]]:
    """(kind of surface to find, what to draw on it), or None."""
    said = (text or "").strip().rstrip(".!?")
    if not _LOOKING.match(said):
        return None
    halves = _JOIN.split(said, maxsplit=1)
    if len(halves) != 2:
        return None
    where, what = halves
    surface = _SURFACE.search(where)
    if not surface:
        return None
    subject = _OF.sub("", _LEAD.sub("", what.strip())).strip()
    return (re.sub(r"\s+", " ", surface.group(1).lower()), subject) if subject else None


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not mine'."""
    pair = parse_find_and_draw(text)
    if pair:
        kind, subject = pair
        from .draw_command import run as draw
        found = await find(kind, f"free online {kind} no signup")
        if not found:
            return f"I couldn't find a {kind} that actually worked, sir."
        await asyncio.sleep(2.0)
        said = await draw(subject, config)
        return f"Found {found.host} and {said[0].lower()}{said[1:]}"

    purpose = parse(text)
    if purpose is None:
        return None
    found = await find(purpose)
    if not found:
        return f"I opened the first few results for {purpose} and none of them worked, sir."
    return f"Opened {found.host} — {found.title[:60]}. I checked it works before saying so."
