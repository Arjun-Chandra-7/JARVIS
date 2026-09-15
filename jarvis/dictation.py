"""Dictation: what you say is typed where the cursor is, not treated as an instruction.

Two different things can be done with a transcript, and the only way to know which is meant is to
be told. In dictation everything is typed verbatim — including sentences that would otherwise be
commands, because "open the door" is a thing people write. Only the phrase that ends dictation is
listened for, and it is short and deliberate so that ordinary prose does not trip it.

Punctuation is spoken the way people dictate: "comma", "full stop", "new line".
"""

from __future__ import annotations

import re
from typing import Optional

_START = re.compile(
    r"^(?:please\s+)?(?:jarvis[,.\s]*)?(?:"
    r"(?:start\s+)?(?:dictation|dictate|dictating)(?:\s+mode)?|"
    r"take\s+(?:a\s+)?(?:dictation|note|this\s+down)|"
    r"type\s+(?:what\s+i\s+say|for\s+me)|"
    r"likhna\s+shuru\s+karo|likho"
    r")\s*$",
    re.IGNORECASE,
)

_STOP = re.compile(
    r"^(?:jarvis[,.\s]*)?(?:"
    r"stop\s+(?:dictation|dictating|typing)|end\s+dictation|done\s+dictating|"
    r"that'?s\s+enough|stop\s+taking\s+(?:this\s+)?down|"
    r"bas\s+karo|likhna\s+band\s+karo"
    r")\s*[.!?]?$",
    re.IGNORECASE,
)

# Said aloud while dictating, meaning the mark rather than the word.
_SPOKEN_MARKS = [
    (r"\bnew\s+paragraph\b", "\n\n"),
    (r"\bnew\s+line\b", "\n"),
    (r"\bfull\s+stop\b", "."),
    (r"\bperiod\b", "."),
    (r"\bcomma\b", ","),
    (r"\bquestion\s+mark\b", "?"),
    (r"\bexclamation\s+(?:mark|point)\b", "!"),
    (r"\bcolon\b", ":"),
    (r"\bsemicolon\b", ";"),
    (r"\bopen\s+(?:bracket|paren(?:thesis)?)\b", "("),
    (r"\bclose\s+(?:bracket|paren(?:thesis)?)\b", ")"),
]


def wants_to_start(text: str) -> bool:
    return bool(_START.match((text or "").strip().rstrip(".!?")))


def wants_to_stop(text: str) -> bool:
    return bool(_STOP.match((text or "").strip()))


def as_typed(text: str) -> str:
    """Spoken words as they should appear on the page."""
    out = (text or "").strip()
    for pattern, mark in _SPOKEN_MARKS:
        out = re.sub(pattern, mark, out, flags=re.IGNORECASE)
    # "hello ." -> "hello." and "a ,b" -> "a, b"
    out = re.sub(r"[ \t]+([.,;:!?)])", r"\1", out)
    out = re.sub(r"([(])[ \t]+", r"\1", out)
    # A spoken line break is a break, not a word with spaces around it.
    out = re.sub(r"[ \t]*\n[ \t]*", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def type_out(text: str) -> bool:
    """Put the words where the cursor is."""
    from .integrations import desktop_control

    body = as_typed(text)
    if not body:
        return False
    return bool(desktop_control.type_text(body + " "))
