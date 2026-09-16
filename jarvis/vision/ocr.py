"""Reading the words on screen, and saying where they are.

This replaces asking a vision model where something is. Measured on the same screen: the model
took eleven seconds to place one target and then failed to confirm its own answer; reading the
whole screen with OCR takes about a second and a half and returns every word with its box. It is
not a better guess — it is not a guess.

The model still has its place for "what am I looking at", which is a question about meaning.
"Where is the Allow button" is a question about text, and text is what OCR is for.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

_engine = None
_engine_lock = threading.Lock()

# One read of a 1920x1080 screen is ~1.3 s, and a click is usually followed by another look at
# the same screen, so a short cache turns a sequence of actions into one read.
CACHE_S = 2.0
_cache: dict = {}


@dataclass
class Word:
    text: str
    x: int          # centre, in real screen pixels
    y: int
    left: int
    top: int
    right: int
    bottom: int
    confidence: float

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        return _engine


def available() -> bool:
    try:
        _get_engine()
        return True
    except Exception:  # noqa: BLE001
        return False


def read(path: Optional[str] = None, scale: Optional[tuple[float, float]] = None) -> list[Word]:
    """Every word on screen, with where it is. Empty when nothing can be read."""
    from . import screenshot

    now = time.time()
    if path is None:
        cached = _cache.get("words")
        if cached and now - cached[0] < CACHE_S:
            return cached[1]
        path = screenshot.capture()
        if not path:
            return []
        geometry = getattr(screenshot, "_last_geom", {}) or {}
        real, image = geometry.get("real"), geometry.get("img")
        if real and image and image[0] and image[1]:
            scale = (real[0] / image[0], real[1] / image[1])

    try:
        result, _ = _get_engine()(path)
    except Exception:  # noqa: BLE001 - a screen that cannot be read is not an error to raise
        return []

    sx, sy = scale or (1.0, 1.0)
    words: list[Word] = []
    for box, text, confidence in (result or []):
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        words.append(Word(
            text=str(text).strip(),
            x=int(sum(xs) / len(xs) * sx), y=int(sum(ys) / len(ys) * sy),
            left=int(min(xs) * sx), top=int(min(ys) * sy),
            right=int(max(xs) * sx), bottom=int(max(ys) * sy),
            confidence=float(confidence),
        ))
    _cache["words"] = (now, words)
    return words


def forget() -> None:
    _cache.clear()


def _flatten(text: str) -> str:
    """For comparison only. OCR runs words together — "New chat - Claude Perplexity" comes back
    as "Newchat-ClaudePerplexity" — so spacing and punctuation cannot be part of the match."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def find(target: str, words: Optional[list[Word]] = None) -> Optional[Word]:
    """The word on screen that best matches `target`, or None when nothing is close enough."""
    wanted = _flatten(target)
    if not wanted:
        return None
    words = read() if words is None else words

    best, best_score = None, 0.0
    for word in words:
        seen = _flatten(word.text)
        if not seen:
            continue
        if seen == wanted:
            score = 1.0
        elif wanted in seen:
            # A label inside a longer run — "allow" within "allowcookies", "whiteboard" within
            # "onlinewhiteboardorg". Scaling by the length ratio scored these below a coin toss
            # and nothing on screen was ever found; extra text around the word is normal, so the
            # penalty for it is small.
            score = 0.9 - 0.15 * (1 - len(wanted) / len(seen))
        elif seen in wanted:
            score = 0.85 - 0.2 * (1 - len(seen) / len(wanted))
        else:
            score = SequenceMatcher(None, wanted, seen).ratio() * 0.85
        score *= 0.6 + 0.4 * word.confidence
        if score > best_score:
            best, best_score = word, score

    # Below this the "match" is a different word that happens to share some letters.
    return best if best_score >= 0.55 else None


def describe(words: Optional[list[Word]] = None, limit: int = 60) -> str:
    """What the screen says, for answering rather than clicking."""
    words = read() if words is None else words
    readable = [w.text for w in sorted(words, key=lambda w: (w.top, w.left))
                if w.text and w.confidence > 0.5]
    if not readable:
        return "I can't read any text on the screen."
    return " · ".join(readable[:limit])
