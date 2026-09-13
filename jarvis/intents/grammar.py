"""A small sentence-template language, compiled to regex.

Borrowed from Rhasspy's `sentences.ini` and OVOS's `.intent` files, trimmed to what is actually
useful here. The point is a deterministic path in front of the model: "set a timer for ten
minutes" should not cost a language-model turn, a tool retrieval over eighty-five schemas, and a
second round trip to read the result back. It should be a regex match and a function call.

Syntax
------
    (a|b|c)             one of these
    [optional words]    may be absent
    {name}              capture free text into the tool argument `name`
    {name:int}          capture a number — digits or words ("twenty five")
    {name:duration}     capture a span and convert it to seconds ("an hour and a half")
    {name:percent}      capture a number, clamped to 0-100
    $slot               one of a list supplied at load time (contacts, apps, playlists)

Capture names ARE tool argument names — there is no separate mapping table to keep in step, which
is the same reasoning behind putting `parallel_safe` on the tool rather than in a list elsewhere.

Everything is anchored and matched against a normalised utterance, so a pattern either fits the
whole sentence or does not fire. Partial matches would be worse than useless: silently
misinterpreting "don't set a timer" as "set a timer" is exactly the failure that makes people stop
trusting a voice assistant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

# --- number words -------------------------------------------------------------------------------
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000}
_ARTICLES = {"a", "an", "the"}

_DURATION_UNITS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
}


def parse_number(text: str) -> Optional[float]:
    """Digits or English words. Returns None when there is no number in `text`."""
    text = (text or "").strip().lower().replace("-", " ")
    if not text:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1))

    total, current, seen = 0.0, 0.0, False
    for word in text.split():
        if word in ("and",):
            continue
        if word in _ARTICLES:
            continue
        if word in _UNITS:
            current += _UNITS[word]
            seen = True
        elif word in _TENS:
            current += _TENS[word]
            seen = True
        elif word in _SCALES:
            current = (current or 1) * _SCALES[word]
            seen = True
        elif word == "half":
            current += 0.5
            seen = True
        elif word == "quarter":
            current += 0.25
            seen = True
        elif re.fullmatch(r"\d+(?:\.\d+)?", word):
            current += float(word)
            seen = True
        else:
            return None
    return (total + current) if seen else None


def parse_duration(text: str) -> Optional[int]:
    """"ten minutes", "1 hour 30", "an hour and a half", "90 seconds" -> seconds."""
    text = (text or "").strip().lower().replace("-", " ")
    # "a quarter of an hour" / "three quarters of an hour" — the "of an" is grammar, not quantity.
    text = re.sub(r"\bof\s+(an?|the)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    total = 0.0
    matched = False
    last_unit = 0                          # seconds-per-unit of the most recent unit seen
    # Walk number/unit pairs left to right so compound spans accumulate.
    pattern = re.compile(
        r"([\d.]+|(?:[a-z]+\s+)*?[a-z]+?)\s*(" + "|".join(sorted(_DURATION_UNITS, key=len, reverse=True)) + r")\b"
    )
    pos = 0
    for m in pattern.finditer(text):
        head = m.group(1).strip()
        qty = parse_number(head)
        if qty is None:
            # "an hour" / "the minute" — the article is the quantity. Anything else is not a
            # number at all, and "a timer for banana minutes" must fail rather than mean one.
            if head and head not in _ARTICLES:
                return None
            qty = 1.0
        last_unit = _DURATION_UNITS[m.group(2)]
        total += qty * last_unit
        matched = True
        pos = m.end()
    tail = text[pos:].strip()
    if matched and tail:
        # "an hour and a half" means half of the *preceding* unit, while "1 hour 30" means
        # thirty of the next smaller one. The fraction words are the tell.
        fraction = re.fullmatch(r"(?:and\s+)?(?:a\s+)?(half|quarter)", tail)
        if fraction:
            total += (0.5 if fraction.group(1) == "half" else 0.25) * last_unit
        else:
            extra = parse_number(tail)
            if extra is None:
                return None        # unconsumed words: this is not purely a duration
            total += extra * 60
    if not matched:
        # A bare number with no unit is taken as minutes, which is how people speak.
        n = parse_number(text)
        if n is None:
            return None
        total = n * 60
    return int(round(total))


CONVERTERS: dict[str, Callable[[str], object]] = {
    "int": lambda s: (lambda n: None if n is None else int(n))(parse_number(s)),
    "duration": parse_duration,
    "percent": lambda s: (lambda n: None if n is None else max(0, min(100, int(n))))(parse_number(s)),
    "text": lambda s: s.strip(),
}


# --- template compilation -----------------------------------------------------------------------
@dataclass
class Compiled:
    regex: re.Pattern
    converters: dict[str, str]      # capture name -> converter name


def _expand_alternatives(template: str) -> str:
    """`(a|b)` and `[x]` become regex groups. Nesting is supported; escaping is not needed."""
    out = []
    for ch in template:
        out.append(ch)
    return "".join(out)


_TOKEN = re.compile(r"\{(\w+)(?::(\w+))?\}|\$(\w+)|(\()|(\))|(\[)|(\])|(\|)|([^{}$()\[\]|]+)")


def compile_template(template: str, slots: Optional[dict[str, Iterable[str]]] = None) -> Compiled:
    """Turn one sentence template into an anchored regex plus its converter map."""
    slots = slots or {}
    converters: dict[str, str] = {}
    parts: list[str] = []

    for m in _TOKEN.finditer(template):
        name, conv, slot, lpar, rpar, lbrk, rbrk, pipe, literal = m.groups()
        if name:
            converters[name] = conv or "text"
            # A converted capture is greedy over words; a free-text one stops at the end.
            parts.append(f"(?P<{name}>.+?)" if conv else f"(?P<{name}>.+)")
        elif slot:
            options = sorted({str(v) for v in slots.get(slot, ())}, key=len, reverse=True)
            if not options:
                # An unfilled slot can never match; make that explicit rather than matching all.
                parts.append("(?!)")
            else:
                converters[slot] = "text"
                parts.append(f"(?P<{slot}>" + "|".join(re.escape(o) for o in options) + ")")
        elif lpar:
            parts.append("(?:")
        elif rpar:
            parts.append(")")
        elif lbrk:
            parts.append("(?:")
        elif rbrk:
            parts.append(")?")
        elif pipe:
            parts.append("|")
        elif literal:
            # Collapse runs of whitespace to a flexible separator so spacing never matters.
            chunk = re.escape(literal.strip())
            chunk = chunk.replace(r"\ ", r"\s+")
            if literal.startswith(" ") and parts:
                parts.append(r"\s*")
            parts.append(chunk)
            if literal.endswith(" "):
                parts.append(r"\s*")

    return Compiled(re.compile(r"^\s*" + "".join(parts) + r"\s*$", re.IGNORECASE), converters)


def normalise(utterance: str) -> str:
    """Lowercase, strip punctuation and filler, collapse whitespace."""
    text = (utterance or "").lower().strip()
    text = re.sub(r"^(hey |ok |okay )?jarvis[,\s]+", "", text)
    text = re.sub(r"\b(please|could you|can you|would you)\b", " ", text)
    text = re.sub(r"[^\w\s:%.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match(compiled: Compiled, utterance: str) -> Optional[dict]:
    """Return the captured arguments, or None. A failed conversion is a failed match."""
    m = compiled.regex.match(utterance)
    if not m:
        return None
    args: dict[str, object] = {}
    for key, raw in (m.groupdict() or {}).items():
        if raw is None:
            continue
        value = CONVERTERS.get(compiled.converters.get(key, "text"), CONVERTERS["text"])(raw)
        if value is None:
            # "set a timer for banana minutes" parses structurally but has no number in it.
            return None
        args[key] = value
    return args
