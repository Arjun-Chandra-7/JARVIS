"""Resolve rote commands to a tool call without involving the language model.

Roughly two thirds of what people say to an assistant is a handful of fixed shapes: set a timer,
volume up, what's the time, pause the music, lock the screen. Sending those through a 3B model —
which must read a retrieved menu of tools, emit a tool call, read the result back, and write a
sentence about it — costs seconds and sometimes gets it wrong.

This is the fast path. `.intent` files declare sentence templates against a tool name; a match
produces a tool call directly. A miss falls through to the model, so the model remains the general
case and this is only ever an optimisation.

Two rules keep it honest:

  * Patterns are anchored. A template matches the whole utterance or not at all, so "don't set a
    timer" cannot fire "set a timer".
  * Anything with a question mark, or any utterance long enough to be a real request, is handed
    straight to the model. The fast path is for commands, not conversation.

An `.intent` file is a tool name in brackets followed by its templates:

    [set_timer]
    (set|start) a timer for {seconds:duration}
    remind me in {seconds:duration}

Capture names are the tool's argument names. Add a file, get a fast path — no code change.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .grammar import Compiled, compile_template, match, normalise

INTENT_DIR = Path(__file__).resolve().parent / "rules"

# An utterance longer than this is a request, not a command; don't try to pattern-match it.
MAX_WORDS = 12


@dataclass
class Rule:
    tool: str
    template: str
    compiled: Compiled
    source: str = ""

    def try_match(self, text: str) -> Optional[dict]:
        return match(self.compiled, text)


@dataclass
class Resolution:
    """A command recognised without the model. `say` is spoken instead of the tool result when
    the tool's own output is not a sentence — "done." reads better than "volume set to 40"."""

    tool: str
    args: dict
    template: str
    say: Optional[str] = None


def enabled() -> bool:
    return os.environ.get("JARVIS_INTENTS", "1").strip().lower() not in {"0", "false", "no", "off"}


def parse_intent_file(text: str, slots: Optional[dict] = None, source: str = "") -> list[Rule]:
    rules: list[Rule] = []
    tool: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = re.fullmatch(r"\[([a-z_0-9]+)\]", line)
        if header:
            tool = header.group(1)
            continue
        if tool is None:
            continue                      # a template before any [tool] header is ignored
        rules.append(Rule(tool=tool, template=line,
                          compiled=compile_template(line, slots), source=source))
    return rules


def load_rules(slots: Optional[dict] = None, directory: Optional[Path] = None) -> list[Rule]:
    directory = directory or INTENT_DIR
    rules: list[Rule] = []
    if not directory.is_dir():
        return rules
    for path in sorted(directory.glob("*.intent")):
        try:
            rules.extend(parse_intent_file(path.read_text(encoding="utf-8"), slots, path.name))
        except OSError:
            continue
    return rules


def live_slots(config) -> dict[str, list[str]]:
    """Fill `$contacts` and friends from real data.

    This is what makes the fast path safe on a small model: "message Priya" resolves against the
    actual address book rather than the model guessing at a name it half-heard.
    """
    slots: dict[str, list[str]] = {"contacts": [], "apps": []}
    try:
        from ..integrations import contacts

        slots["contacts"] = [str(n) for n in contacts.all_names(config)][:800]
    except Exception:  # noqa: BLE001 - a missing address book just means that slot never matches
        pass
    try:
        from ..integrations import apps

        slots["apps"] = [str(n) for n in apps.known_names()][:400]
    except Exception:  # noqa: BLE001
        pass
    return slots


class IntentRouter:
    """Compiled rules plus the matching policy. Cheap to keep around; rebuild to pick up new files."""

    def __init__(self, rules: Optional[list[Rule]] = None, slots: Optional[dict] = None) -> None:
        self.rules = rules if rules is not None else load_rules(slots)

    def resolve(self, utterance: str) -> Optional[Resolution]:
        if not enabled() or not self.rules:
            return None
        raw = (utterance or "").strip()
        if not raw:
            return None
        # Punctuation is not consulted. An earlier version refused anything containing "?", which
        # sounds prudent but made "who is around?" miss while "who is around" hit — and the
        # anchoring below is what actually provides the safety: a template matches the whole
        # normalised utterance or not at all.
        text = normalise(raw)
        if not text or len(text.split()) > MAX_WORDS:
            return None
        for rule in self.rules:
            args = rule.try_match(text)
            if args is None:
                continue
            args = {k: v for k, v in args.items() if not k.startswith("_")}
            return Resolution(tool=rule.tool, args=self._stringify(args), template=rule.template)
        return None

    @staticmethod
    def _stringify(args: dict) -> dict:
        """Tools take the same loose string arguments a model would send, so behaviour is
        identical whichever path produced the call."""
        return {k: (str(v) if not isinstance(v, str) else v) for k, v in args.items()}

    def explain(self, utterance: str) -> str:
        r = self.resolve(utterance)
        return "no match" if r is None else f"{r.tool}({r.args}) via {r.template!r}"
