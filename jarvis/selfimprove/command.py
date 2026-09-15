"""Asking Jarvis to look at what it has been getting wrong."""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from . import improve, journal

_ASK = re.compile(
    r"^(?:please\s+)?(?:"
    r"(?:fix|improve|repair|debug)\s+(?:yourself|your\s+own\s+code|your\s+code)|"
    r"learn\s+from\s+your\s+mistakes|"
    r"what\s+(?:have\s+you|are\s+you)\s+(?:been\s+)?(?:getting|got)\s+wrong|"
    r"what\s+(?:keeps\s+)?(?:going|went)\s+wrong"
    r")\s*\??$",
    re.IGNORECASE,
)

_REPORT_ONLY = re.compile(r"what\s+|wrong", re.IGNORECASE)


def _describe(pairs) -> str:
    if not pairs:
        return "Nothing has gone wrong more than once lately, sir."
    lines = [f"{n} times: {f.detail[:90]}" for n, f in pairs[:3]]
    return "What keeps going wrong — " + "; ".join(lines) + "."


async def handle(text: str, _config=None) -> Optional[str]:
    """None means 'not mine'."""
    said = (text or "").strip()
    if not _ASK.match(said.rstrip(".!?")):
        return None

    pairs = journal.recurring(minimum=2)
    # "What keeps going wrong" is a question; only "fix yourself" spends a model on it.
    if _REPORT_ONLY.search(said) and not re.search(r"\bfix|improve|repair|debug|learn\b", said, re.I):
        return _describe(pairs)

    if not pairs:
        return "Nothing has failed twice lately, sir — there is nothing worth rewriting."

    times, failure = pairs[0]
    result = await asyncio.to_thread(improve.attempt, failure, times)
    if not result.ok:
        return f"I tried to fix “{failure.detail[:60]}” and could not: {result.summary[:160]}"
    where = f" and pushed it as {result.branch}" if result.pushed else f" on branch {result.branch}"
    return (f"I've proposed a fix for something that failed {times} times{where}. "
            f"The suite passes — {result.tests}. It needs your review before it goes in.")
