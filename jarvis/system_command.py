"""Volume, brightness and mute — resolved deterministically, without asking the model.

The same lesson as «open X» in open_command, measured on the same machine:

    "set brightness to 30 percent"  ->  "I don't have a tool for that."

The tool was registered, the router put it first in the shortlist, and the local 3B brain still
declined to call it and then claimed the brightness had been set. These commands have one meaning,
one integer argument and no ambiguity, so inference buys nothing and loses turns.

Every reply here is checked against the machine afterwards. A command that could not be carried
out says why, and says what would fix it — a refusal the user can act on beats a cheerful lie.
"""

from __future__ import annotations

import re
from typing import Optional

_WORD_NUMBERS = {
    "zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "half": 50, "full": 100, "max": 100, "maximum": 100, "min": 1, "minimum": 1,
}

_WHAT = r"(?P<what>volume|sound|audio|brightness|screen\s+brightness|display|backlight|keyboard(?:\s+backlight)?)"

# "set the volume to 40", "volume 40 percent", "put brightness at 60"
_SET_RE = re.compile(
    rf"""^(?:please\s+)?
        (?:(?:set|put|change|make|turn)\s+)?
        (?:the\s+|my\s+)?
        {_WHAT}
        \s*(?:to|at|=)?\s*
        (?P<value>\d{{1,3}}|[a-z]+)
        \s*(?:%|percent|per\s*cent)?
        \s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# "turn the volume up", "brightness down a bit", "louder", "quieter"
_STEP_RE = re.compile(
    rf"""^(?:please\s+)?
        (?:turn\s+)?
        (?:the\s+|my\s+)?
        {_WHAT}
        \s+(?P<dir>up|down|louder|quieter)
        (?:\s+a\s+(?:bit|little|lot))?
        \s*$""",
    re.IGNORECASE | re.VERBOSE,
)

_SHORT_STEP_RE = re.compile(
    r"^(?:please\s+)?(?:turn\s+it\s+)?(?P<dir>louder|quieter|volume\s+up|volume\s+down)\s*$",
    re.IGNORECASE,
)

_MUTE_RE = re.compile(r"^(?:please\s+)?(?P<act>mute|unmute|silence)(?:\s+(?:the\s+)?(?:sound|volume|audio))?\s*$",
                      re.IGNORECASE)

# VERBOSE mode strips literal spaces, so "| is" silently became "|is" and "what is the volume"
# did not match while "whats the volume" did. Every space here is written as \s.
_ASK_RE = re.compile(
    rf"""^(?:what(?:'?s|\s+is)|how\s+(?:loud|bright))
        \s+(?:the\s+|my\s+)?
        {_WHAT}
        (?:\s+(?:set\s+)?(?:to|at))?
        \s*\??$""",
    re.IGNORECASE | re.VERBOSE,
)

# The other word order: "how bright is the screen", "how loud is it".
_ASK_ADJ_RE = re.compile(
    r"""^how\s+(?P<adj>loud|bright)\s+is\s+(?:the\s+|my\s+)?
        (?:it|screen|display|volume|sound|audio|brightness)\s*\??$""",
    re.IGNORECASE | re.VERBOSE,
)

# Speech puts things in front of the command — a mis-heard wake word most often. "Hey Jarvis, set
# my volume to 70" came back as "He always set my volume to 70%", matched nothing, and the model
# answered "I cannot set brightness directly. Please use `open_app` for web browsers."
#
# An explicit verb is required here, so a passing mention ("the volume of a sphere", "turn down
# that offer") cannot move anything.
_LOOSE_SET_RE = re.compile(
    rf"""\b(?:set|put|change|make|turn)\s+
        (?:the\s+|my\s+)?
        {_WHAT}
        \s*(?:to|at|=)\s*
        (?P<value>\d{{1,3}}|[a-z]+)
        \s*(?:%|percent|per\s*cent)?
        \s*$""",
    re.IGNORECASE | re.VERBOSE,
)

_LOOSE_STEP_RE = re.compile(
    rf"""\bturn\s+(?:the\s+|my\s+)?{_WHAT}\s+(?P<dir>up|down)
        (?:\s+a\s+(?:bit|little|lot))?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

_STEP = 10


def _to_percent(value: str) -> Optional[int]:
    text = (value or "").strip().lower()
    if text.isdigit():
        n = int(text)
        return n if 0 <= n <= 100 else None
    return _WORD_NUMBERS.get(text)


def _kind(what: str) -> str:
    low = (what or "").lower()
    if low.startswith("keyboard"):
        return "keyboard"
    if "volume" in low or "sound" in low or "audio" in low:
        return "volume"
    return "brightness"


def parse(text: str) -> Optional[dict]:
    """{action, kind, value} for a system-control command, or None when it is not one."""
    cleaned = (text or "").strip().rstrip(".!?")
    if not cleaned:
        return None

    m = _MUTE_RE.match(cleaned)
    if m:
        return {"action": "mute", "kind": "volume",
                "value": m.group("act").lower() != "unmute"}

    m = _ASK_RE.match(cleaned)
    if m:
        return {"action": "read", "kind": _kind(m.group("what")), "value": None}

    m = _ASK_ADJ_RE.match(cleaned)
    if m:
        return {"action": "read",
                "kind": "volume" if m.group("adj").lower() == "loud" else "brightness",
                "value": None}

    m = _STEP_RE.match(cleaned) or _SHORT_STEP_RE.match(cleaned)
    if m:
        groups = m.groupdict()
        direction = groups["dir"].lower()
        kind = _kind(groups.get("what") or "volume")
        up = direction in ("up", "louder") or direction.endswith("up")
        return {"action": "step", "kind": kind, "value": _STEP if up else -_STEP}

    m = _SET_RE.match(cleaned)
    if m:
        pct = _to_percent(m.group("value"))
        if pct is None:
            return None
        return {"action": "set", "kind": _kind(m.group("what")), "value": pct}

    # Nothing matched from the start of the line; allow a run-up for garbled speech.
    m = _LOOSE_SET_RE.search(cleaned)
    if m:
        pct = _to_percent(m.group("value"))
        if pct is not None:
            return {"action": "set", "kind": _kind(m.group("what")), "value": pct}

    m = _LOOSE_STEP_RE.search(cleaned)
    if m:
        up = m.group("dir").lower() == "up"
        return {"action": "step", "kind": _kind(m.group("what")),
                "value": _STEP if up else -_STEP}
    return None


def _read(kind: str) -> Optional[int]:
    """The machine's current value as a number. get_volume reports a string like "35%"."""
    from .integrations import system_control as sc

    if kind == "volume":
        raw = sc.get_volume()
        if raw is None:
            return None
        digits = re.sub(r"[^\d]", "", str(raw))
        return int(digits) if digits else None
    if kind == "brightness":
        return sc.get_brightness()
    return None


def _apply(kind: str, pct: int) -> bool:
    from .integrations import system_control as sc

    if kind == "volume":
        return bool(sc.set_volume(pct))
    if kind == "keyboard":
        return bool(sc.set_keyboard_brightness(pct))
    return bool(sc.set_brightness(pct))


def _why_not(kind: str) -> str:
    from .integrations import system_control as sc

    if kind in ("brightness", "keyboard"):
        return sc.brightness_blocker() or f"I could not change the {kind}."
    return f"I could not change the {kind}."


def run(command: dict) -> str:
    """Carry it out and report what the machine actually says afterwards."""
    kind, action = command["kind"], command["action"]

    if action == "mute":
        from .integrations import system_control as sc

        wanted = bool(command["value"])
        if not sc.mute_audio(wanted):
            return "I couldn't reach the audio controls."
        return "Muted." if wanted else "Unmuted."

    if action == "read":
        now = _read(kind)
        if now is None:
            return _why_not(kind)
        return f"{kind.capitalize()} is at {now}%."

    before = _read(kind)
    if action == "step":
        if before is None:
            return _why_not(kind)
        target = max(0, min(100, before + int(command["value"])))
    else:
        target = int(command["value"])

    if not _apply(kind, target):
        return _why_not(kind)

    # Ask the machine rather than trusting the call: brightnessctl exits non-zero when it is
    # refused, but a silent no-op is exactly what this whole module exists to catch.
    after = _read(kind)
    if after is None:
        return f"{kind.capitalize()} set to {target}%."
    if abs(after - target) > 5 and after == before:
        return _why_not(kind)
    return f"{kind.capitalize()} is at {after}%."


async def handle(text: str, _config=None) -> Optional[str]:
    """Entry point for the deterministic command layer. None means 'not mine'."""
    import asyncio

    command = parse(text)
    if command is None:
        return None
    try:
        return await asyncio.to_thread(run, command)
    except Exception as exc:  # noqa: BLE001 - never swallow a turn; let the model try instead
        return None if isinstance(exc, (ImportError, AttributeError)) else \
            f"I couldn't change the {command['kind']} — {exc}"
