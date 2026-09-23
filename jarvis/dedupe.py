"""One spoken sentence, acted on once.

The same utterance can reach /chat twice — the voice loop and the overlay both forwarding it, a
retry after a slow reply — and a sentence that sends a message or pauses a video must not do it
twice. Two checks:

* an ``event_id`` the sender attaches: the same id is the same event for a minute;
* the words themselves: the same sentence again within ``window_s`` of the first one *finishing*
  is the same event arriving by another path. /chat handles one turn at a time, so a duplicate
  queued behind the original arrives just after it completes.

Saying the same thing again deliberately takes longer than that — a capture, the recogniser, a
reply spoken — so a real repeat still goes through.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Optional


def fingerprint(text: str) -> str:
    from .commands import clean_text
    said = re.sub(r"[^\w\s]", " ", clean_text(text or "").lower())
    said = re.sub(r"\s+", " ", said).strip()
    return hashlib.sha256(said.encode()).hexdigest()[:16] if said else ""


class Deduper:
    def __init__(self, window_s: float = 1.5, id_ttl_s: float = 60.0, clock=time.monotonic) -> None:
        self.window_s, self.id_ttl_s, self.clock = window_s, id_ttl_s, clock
        self._ids: dict[str, tuple[float, str]] = {}
        self._recent: dict[str, tuple[float, str]] = {}

    def seen(self, text: str, event_id: str = "") -> Optional[str]:
        """The reply already given when this is a duplicate, else None."""
        now = self.clock()
        self._ids = {k: v for k, v in self._ids.items() if now - v[0] <= self.id_ttl_s}
        if event_id and event_id in self._ids:
            return self._ids[event_id][1]
        fp = fingerprint(text)
        hit = self._recent.get(fp) if fp else None
        if hit and now - hit[0] <= self.window_s:
            return hit[1]
        return None

    def done(self, text: str, reply: str, event_id: str = "") -> None:
        now = self.clock()
        if event_id:
            self._ids[event_id] = (now, reply)
        fp = fingerprint(text)
        if fp:
            self._recent = {k: v for k, v in self._recent.items() if now - v[0] <= self.window_s}
            self._recent[fp] = (now, reply)


CHAT = Deduper()
