"""A record of what Jarvis failed to do, in its own words, at the moment it failed.

Everything here already existed as a refusal the user heard once and nobody kept: a tool that
reported it could not act, a task that stopped at step three, a reply caught claiming something
that never happened. Each of those is a description of a real defect, written with the exact
inputs that produced it, which is the hard part of a bug report.

Kept as JSON lines, in the vault rather than the repo, because this is a record of what happened
on this machine and not part of the program.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

MAX_ENTRIES = 500


def _path() -> Path:
    base = os.environ.get("JARVIS_JOURNAL")
    if base:
        return Path(base)
    return Path.home() / ".local" / "share" / "jarvis" / "failures.jsonl"


@dataclass
class Failure:
    kind: str                    # what sort of thing went wrong
    request: str                 # what the user actually asked for
    detail: str                  # what Jarvis said, or the error
    where: str = ""              # the step or component
    at: float = field(default_factory=time.time)

    def signature(self) -> str:
        """What makes two failures the same problem rather than two occurrences of it."""
        words = re.sub(r"[^a-z ]+", " ", f"{self.kind} {self.where} {self.detail}".lower())
        return " ".join(sorted(set(words.split())))[:200]


def record(kind: str, request: str, detail: str, where: str = "") -> None:
    """Note a failure. Never raises: a problem writing this must not become a second problem."""
    try:
        entry = Failure(kind=kind, request=(request or "")[:400],
                        detail=(detail or "")[:600], where=where[:80])
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def read(limit: int = MAX_ENTRIES) -> list[Failure]:
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(Failure(**json.loads(line)))
        except (ValueError, TypeError):
            continue
    return out


def recurring(minimum: int = 2, since_s: float = 7 * 24 * 3600) -> list[tuple[int, Failure]]:
    """Failures that have happened more than once, commonest first.

    Something that went wrong once may have been the weather. Something that goes wrong every
    time is a defect, and worth spending a model's attention on.
    """
    cutoff = time.time() - since_s
    groups: dict[str, list[Failure]] = {}
    for entry in read():
        if entry.at < cutoff:
            continue
        groups.setdefault(entry.signature(), []).append(entry)
    counted = [(len(v), v[-1]) for v in groups.values() if len(v) >= minimum]
    return sorted(counted, key=lambda pair: pair[0], reverse=True)


def clear() -> None:
    try:
        _path().unlink()
    except OSError:
        pass
