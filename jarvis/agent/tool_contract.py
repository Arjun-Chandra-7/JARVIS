"""Validate and repair tool calls before they run, and describe how they ended.

A small model gets the tool right far more often than it gets the arguments right. Measured on
qwen2.5:3b over the Jarvis command set, the surviving failures were all argument shape, not tool
choice:

    set_timer     {"minutes": 5}              -> required "seconds" missing
    set_timer     {"duration_seconds": "300"} -> required "seconds" missing, string not int
    set_volume    {"level": "30"}             -> required "percent" missing
    media_control {"state": "pause"}          -> required "action" missing
    set_reminder  {"text": "Call Mum at 6"}   -> required "when" missing

Three of those are mechanical and safe to fix (a string that is plainly an integer, a name that
contains the real one). The rest are genuine ambiguity, and guessing there would invent user
intent — so they come back as a precise, actionable message the model can retry against once,
instead of silently running a wrong call or failing mutely.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import get_close_matches
from enum import Enum
from typing import Any, Optional


class Outcome(str, Enum):
    """How a tool call ended. Every path through dispatch produces exactly one of these.

    UNCERTAIN matters: a tool that timed out, or whose side effect could not be confirmed, has
    not necessarily failed. Reporting it as failure invites a duplicate retry of something that
    may already have happened — sending the same WhatsApp message twice, for instance.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


@dataclass
class ToolResult:
    outcome: Outcome
    text: str
    tool: str = ""
    args: dict = field(default_factory=dict)
    repaired: list[str] = field(default_factory=list)
    call_id: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.SUCCESS

    def for_model(self) -> str:
        """What the model sees. The outcome is stated, so it cannot read a failure as a success."""
        if self.outcome is Outcome.SUCCESS:
            return self.text
        return f"[{self.outcome.value}] {self.text}"


@dataclass
class Validation:
    ok: bool
    args: dict
    problems: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)

    def message(self, tool: str, schema: dict) -> str:
        """A correction the model can act on: what was wrong, and exactly what the tool wants."""
        props = schema.get("function", {}).get("parameters", {}).get("properties", {})
        required = schema.get("function", {}).get("parameters", {}).get("required", []) or []
        spec = []
        for name, meta in props.items():
            bits = meta.get("type", "string")
            if name in required:
                bits += ", required"
            desc = meta.get("description")
            spec.append(f"{name} ({bits})" + (f": {desc}" if desc else ""))
        return (
            f"Your call to {tool} was not run: {'; '.join(self.problems)}. "
            f"{tool} takes: {'; '.join(spec) if spec else 'no arguments'}. "
            f"Call it again with the correct argument names."
        )


def _coerce_scalar(value: Any, want: str) -> tuple[Any, bool]:
    """Return (value, changed). Only conversions that cannot change meaning are allowed."""
    if want == "integer" and not isinstance(value, bool):
        if isinstance(value, int):
            return value, False
        if isinstance(value, float) and value.is_integer():
            return int(value), True
        if isinstance(value, str):
            s = value.strip().rstrip("%").strip()
            try:
                return int(s), True
            except ValueError:
                try:
                    f = float(s)
                except ValueError:
                    return value, False
                if f.is_integer():
                    return int(f), True
        return value, False
    if want == "number" and isinstance(value, str):
        try:
            return float(value.strip()), True
        except ValueError:
            return value, False
    if want == "boolean":
        if isinstance(value, bool):
            return value, False
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "yes", "on", "1"):
                return True, True
            if low in ("false", "no", "off", "0"):
                return False, True
        return value, False
    if want == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value), True
    return value, False


def _rename_candidate(supplied: str, missing: list[str]) -> Optional[str]:
    """Map an unknown argument name onto a missing one, only when it is unambiguous.

    Containment is the reliable signal — `duration_seconds` plainly means `seconds`. Fuzzy
    similarity is allowed only for near-identical spellings, so `level` never becomes `percent`.
    """
    s = supplied.lower()
    contained = [m for m in missing if m.lower() in s or s in m.lower()]
    if len(contained) == 1:
        return contained[0]
    close = get_close_matches(s, [m.lower() for m in missing], n=2, cutoff=0.82)
    if len(close) == 1:
        for m in missing:
            if m.lower() == close[0]:
                return m
    return None


def validate(schema: dict, args: Optional[dict]) -> Validation:
    """Check args against a registered JSON schema, repairing only what is safe to repair."""
    params = schema.get("function", {}).get("parameters", {}) or {}
    props = params.get("properties", {}) or {}
    required = list(params.get("required", []) or [])
    args = dict(args or {})
    problems: list[str] = []
    repaired: list[str] = []

    # 1) Unknown names: rename onto a missing parameter when unambiguous, else report.
    unknown = [k for k in args if k not in props]
    for key in unknown:
        missing = [r for r in props if r not in args]
        target = _rename_candidate(key, missing)
        if target:
            args[target] = args.pop(key)
            repaired.append(f"{key} -> {target}")
        else:
            args.pop(key)
            problems.append(f"unknown argument '{key}'")

    # 2) Types: coerce the unambiguous cases.
    for key, value in list(args.items()):
        want = props.get(key, {}).get("type")
        if not want:
            continue
        new, changed = _coerce_scalar(value, want)
        if changed:
            args[key] = new
            repaired.append(f"{key}: {type(value).__name__} -> {want}")
        elif want == "integer" and not isinstance(new, int):
            problems.append(f"'{key}' must be an integer, got {value!r}")
        elif want == "number" and not isinstance(new, (int, float)):
            problems.append(f"'{key}' must be a number, got {value!r}")

    # 3) Required parameters must be present and non-empty.
    for name in required:
        if name not in args or args[name] in (None, ""):
            problems.append(f"missing required argument '{name}'")

    return Validation(ok=not problems, args=args, problems=problems, repaired=repaired)


def resolve_name(name: str, known: list[str]) -> tuple[Optional[str], str]:
    """Map a called tool name onto a registered one. Returns (resolved, note)."""
    if name in known:
        return name, ""
    lowered = {k.lower(): k for k in known}
    if name.lower() in lowered:
        return lowered[name.lower()], f"matched '{name}' to '{lowered[name.lower()]}'"
    close = get_close_matches(name.lower(), list(lowered), n=1, cutoff=0.86)
    if close:
        return lowered[close[0]], f"matched '{name}' to '{lowered[close[0]]}'"
    return None, f"no tool named '{name}'"


def parse_arguments(raw: Any) -> tuple[dict, Optional[str]]:
    """Tolerate the JSON a small model actually emits. Returns (args, error)."""
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str):
        return {}, f"arguments were {type(raw).__name__}, expected an object"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Models sometimes wrap the object in prose or a code fence.
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return {}, "arguments were not valid JSON"
        else:
            return {}, "arguments were not valid JSON"
    if not isinstance(parsed, dict):
        return {}, "arguments must be a JSON object"
    return parsed, None
