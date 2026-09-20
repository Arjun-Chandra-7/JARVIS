"""Facts that stop being true, without pretending they never were.

The vault has no way to say a fact has been replaced. When a contact changes number, or a laptop
is replaced, or a job changes, the old line and the new line both sit in the index at almost
identical embedding distance, and both come back. Similarity cannot tell you which one is
current, because currency is not a property of the text — it is a property of time, and nothing
was recording time.

This is the one idea from the 2026 memory literature that survives contact with a controlled
benchmark: bi-temporal validity. Every fact carries when it *became* true and, once it stops,
when it stopped. The old line is not deleted; its window is closed. So two questions can both be
answered from the same file:

    what is true now          the lines whose window is still open
    what was true in March    the lines whose window contained March

Deleting instead of closing answers the first and destroys the second. That matters more than it
sounds: "you told me in March that the demo was on the fourth" is the kind of thing an assistant
is *for*, and it is unanswerable in a store that only ever holds the present.

The notation
------------
An HTML comment, because the vault is Obsidian markdown that the user reads and edits by hand:

    - Machine: ThinkPad X1 <!-- since:2024-01-02 until:2026-03-04 -->
    - Machine: Bhramastra <!-- since:2026-03-04 -->

Obsidian renders neither comment, so the file still reads as a list of facts. A line with no
comment at all is treated as true and always has been, which is what every line in the vault
today already is — so this costs nothing to adopt and nothing to ignore.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

_STAMP = re.compile(r"<!--\s*(?P<body>(?:since|until):[^>]*?)\s*-->\s*$")
_FIELD = re.compile(r"(since|until):(\d{4}-\d{2}-\d{2})")

# "- Key: value" — the shape every line in the profile already has.
_BULLET = re.compile(r"^\s*[-*]\s*(?P<key>[^:]+):\s*(?P<value>.*?)\s*$")


@dataclass(frozen=True)
class Fact:
    """One line of the profile, and the window it is true in."""

    key: str
    value: str
    since: Optional[date] = None
    until: Optional[date] = None
    raw: str = ""

    def holds_on(self, when: date) -> bool:
        """Whether this was true on a given day.

        A missing `since` means "as far back as anyone recorded", which is the right reading for
        every line already in the vault. A missing `until` means it has not been replaced.
        """
        if self.since is not None and when < self.since:
            return False
        if self.until is not None and when >= self.until:
            return False
        return True


def _parse_stamp(line: str) -> tuple[Optional[date], Optional[date]]:
    match = _STAMP.search(line)
    if not match:
        return None, None
    fields = dict(_FIELD.findall(match.group("body")))
    def _as_date(value: Optional[str]) -> Optional[date]:
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            return None
    return _as_date(fields.get("since")), _as_date(fields.get("until"))


def parse(line: str) -> Optional[Fact]:
    """One markdown bullet as a fact, or None when the line is not one."""
    without_stamp = _STAMP.sub("", line).rstrip()
    bullet = _BULLET.match(without_stamp)
    if not bullet:
        return None
    since, until = _parse_stamp(line)
    return Fact(key=bullet.group("key").strip(), value=bullet.group("value").strip(),
                since=since, until=until, raw=line.rstrip("\n"))


def stamp(line: str, since: Optional[date] = None, until: Optional[date] = None) -> str:
    """The line with its window written on it, replacing any window already there."""
    body = _STAMP.sub("", line).rstrip()
    parts = []
    if since is not None:
        parts.append(f"since:{since.isoformat()}")
    if until is not None:
        parts.append(f"until:{until.isoformat()}")
    return f"{body} <!-- {' '.join(parts)} -->" if parts else body


def current(lines: Iterable[str], on: Optional[date] = None) -> list[Fact]:
    """The facts that were true on a given day — today, unless asked otherwise."""
    when = on or date.today()
    out = []
    for line in lines:
        fact = parse(line)
        if fact is not None and fact.holds_on(when):
            out.append(fact)
    return out


def history(lines: Iterable[str], key: str) -> list[Fact]:
    """Everything ever recorded under one key, oldest first.

    This is what makes "what did you have before" answerable, and it only exists because the old
    line was closed rather than removed.
    """
    matching = [f for f in (parse(line) for line in lines) if f and f.key.lower() == key.lower()]
    return sorted(matching, key=lambda f: (f.since or date.min))


def supersede(lines: list[str], key: str, value: str,
              on: Optional[date] = None) -> tuple[list[str], bool]:
    """Record a new value for `key`, closing whatever it replaces.

    Returns the rewritten lines and whether anything was actually superseded, so a caller can
    tell "this is new" from "this changed" — which are different things to say out loud.
    """
    when = on or date.today()
    changed = False
    out: list[str] = []
    inserted = False

    for line in lines:
        fact = parse(line)
        if fact is None or fact.key.lower() != key.lower() or not fact.holds_on(when):
            out.append(line)
            continue
        if fact.value == value:
            # Already recorded and still open. Saying it again is not a change.
            out.append(line)
            inserted = True
            continue
        out.append(stamp(line, since=fact.since, until=when))
        changed = True
        if not inserted:
            indent = line[: len(line) - len(line.lstrip())]
            out.append(stamp(f"{indent}- {key}: {value}", since=when))
            inserted = True

    if not inserted:
        out.append(stamp(f"- {key}: {value}", since=when))
    return out, changed
