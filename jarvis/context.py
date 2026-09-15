"""What the last turn was about, so this one can refer to it.

The deterministic handlers — «open X», the volume, the screen clicks, the task runner — are fast
and reliable precisely because they do not ask a model anything. The cost of that was total
amnesia: they held no state whatsoever between turns, so every follow-up had to name its target
again in full. "Search Netflix for Friends" worked; "play the second one" could not work, because
nothing remembered there had been a list.

This is the smallest thing that fixes it. One recent-turn record per session, holding what was
opened, what was searched, what the results were, and an open-ended slot for an activity that
spans turns — a game in progress, a form being filled in, anything with a position to keep.

Deliberately short-lived. Context that outlives the conversation stops being helpful and starts
being wrong: an hour later "play the second one" means a different list.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# After this long, a reference to "it" is more likely to be about something new.
STALE_AFTER_S = 15 * 60


@dataclass
class Context:
    site: str = ""              # the last website opened
    app: str = ""               # the last application launched
    target: str = ""            # the last thing acted on, whatever kind
    results: list[str] = field(default_factory=list)   # the last list offered
    activity: dict[str, Any] = field(default_factory=dict)  # a game, a form, anything ongoing
    at: float = 0.0

    def fresh(self) -> bool:
        return bool(self.at) and (time.time() - self.at) < STALE_AFTER_S

    def touch(self) -> None:
        self.at = time.time()


_SESSIONS: dict[str, Context] = {}
# Which conversation is being served right now. The places that learn something worth keeping —
# a search deep inside the browser layer, a launch inside the task runner — are a long way from
# the request that started it, and threading a session id through all of them would touch far
# more code than this is worth.
_CURRENT = "local"


def set_current(session_id: str) -> None:
    global _CURRENT
    _CURRENT = session_id or "local"


def current() -> str:
    return _CURRENT


def of(session_id: str = "local") -> Context:
    return _SESSIONS.setdefault(session_id, Context())


def forget(session_id: str = "local") -> None:
    _SESSIONS.pop(session_id, None)


def note_opened(session_id: str = "", *, site: str = "", app: str = "", target: str = "") -> None:
    ctx = of(session_id or _CURRENT)
    if site:
        ctx.site = site
    if app:
        ctx.app = app
    if target:
        ctx.target = target
    ctx.touch()


def note_results(results: list[str], session_id: str = "") -> None:
    ctx = of(session_id or _CURRENT)
    ctx.results = [r for r in (results or []) if r][:10]
    ctx.touch()


# --------------------------------------------------------------------------- referring back
_ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "last": -1, "next": 2,
}

# "play the second one", "open the third result", "click the last one"
_PICK = re.compile(
    r"\b(?:the\s+)?(?P<which>first|second|third|fourth|fifth|last|next|1st|2nd|3rd|4th|5th)"
    r"\s+(?:one|result|video|song|item|link|option)?\b",
    re.IGNORECASE,
)

# A bare pronoun standing in for the last thing: "close it", "play that".
# Not one inside a contraction: "that's all" is how the user says goodnight, and rewriting the
# "that" in it turned a sleep phrase into nonsense that matched nothing.
_PRONOUN = re.compile(r"\b(?:it|that|this|them|those)\b(?!['’])", re.IGNORECASE)

# Phrases that contain a pronoun but are complete in themselves.
_NOT_A_REFERENCE = re.compile(
    r"^(?:that'?s all|that is all|that'?ll be all|forget it|leave it|stop it|"
    r"do it|got it|is that it|that'?s it)$",
    re.IGNORECASE,
)


def pick_from_results(text: str, ctx: Context) -> Optional[str]:
    """The result the words point at, or None when they do not point at one."""
    if not ctx.fresh() or not ctx.results:
        return None
    match = _PICK.search(text or "")
    if not match:
        return None
    n = _ORDINALS.get(match.group("which").lower())
    if n is None:
        return None
    if n == -1:
        return ctx.results[-1]
    return ctx.results[n - 1] if 0 < n <= len(ctx.results) else None


def resolve(text: str, session_id: str = "local") -> str:
    """Rewrite a follow-up so the handlers see a full request.

    Only ever substitutes something concrete that was actually recorded. A pronoun with nothing
    behind it is left alone — the model can ask what was meant, which is better than this
    guessing and being confidently wrong.
    """
    raw = (text or "").strip()
    if not raw:
        return raw
    ctx = of(session_id)
    if not ctx.fresh():
        return raw

    chosen = pick_from_results(raw, ctx)
    if chosen:
        # "play the second one" -> "play <that title>"
        return _PICK.sub(chosen, raw, count=1).strip()

    if _PRONOUN.search(raw) and not _NOT_A_REFERENCE.match(raw.strip().rstrip(".!?")):
        stand_in = ctx.target or ctx.site or ctx.app
        if stand_in:
            return _PRONOUN.sub(stand_in, raw, count=1).strip()
    return raw
