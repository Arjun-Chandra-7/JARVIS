"""Command words as they actually arrive from a real microphone.

Every phrase here was transcribed from real speech in this house and is in the log. Whisper is
not guessing randomly — it is choosing real words that sound like the ones said, which is why
"whiteboard" becomes "wide board" and "draw me" becomes "drone". Biasing the model with a
vocabulary helps at the source; this catches what still gets through.

Applied to command text only, before anything tries to parse it. Never applied to dictation,
where the words are the point and must be typed as heard.
"""

from __future__ import annotations

import re

# (pattern, replacement). Ordered: longer phrases before the words inside them.
_FIXES: list[tuple[re.Pattern, str]] = [
    # "find a site where you can access the wide-board and runnyam onarisa"
    (re.compile(r"\b(?:wide|white|vibe|why|wild|vibe|light|right)[\s-]*"
                r"(?:board|bored|both|boards)\b", re.I), "whiteboard"),
    (re.compile(r"\bwhite\s*bird\b", re.I), "whiteboard"),
    # "Drone the Mona Lisa here!"  /  "runnyam onarisa"
    (re.compile(r"\b(?:drone|drawn|drow|throw)\s+(?=me\b|the\b|a\b|an\b)", re.I), "draw "),
    (re.compile(r"\brunny\s*am\b", re.I), "draw me"),
    # "…website and Romina Mona Lisa" — "draw me" as it arrives. Not "Android", which is a real
    # word that turns "open android studio" into nonsense; that one is handled where the sentence
    # is known to be about a drawing surface.
    (re.compile(r"\b(?:romina|romita|ramona)\b(?=\s+\w)", re.I), "draw"),
    (re.compile(r"\b(?:ona|onari|on a)\s*ris[ae]\b", re.I), "mona lisa"),
    (re.compile(r"\bmona\s*lis[ae]?\b", re.I), "mona lisa"),
    # dictation
    (re.compile(r"\b(?:dick|dig|dic)\s*tate\b", re.I), "dictate"),
    (re.compile(r"\b(?:dick|dig|dic)\s*tation\b", re.I), "dictation"),
    (re.compile(r"\btake\s+dictation\b", re.I), "dictate"),
    # drawing surfaces
    (re.compile(r"\bcan\s*vas\b", re.I), "canvas"),
    (re.compile(r"\bsketch\s*pad\b", re.I), "sketchpad"),
]


def fix(text: str) -> str:
    """The same sentence with known mishearings of command words put right."""
    out = (text or "")
    for pattern, replacement in _FIXES:
        out = pattern.sub(replacement, out)
    return re.sub(r"\s{2,}", " ", out).strip()
