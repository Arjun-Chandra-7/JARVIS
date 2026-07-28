"""Persistent reminders — survive restarts (unlike in-memory timers).

Stored as JSON in the vault (Jarvis/reminders.json). A background checker fires due reminders with a
desktop notification and, in voice mode, a spoken alert. Set the announcer to speak; it always
notifies via notify-send regardless.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

_announce: Optional[Callable[[str], None]] = None


def set_announcer(fn: Optional[Callable[[str], None]]) -> None:
    global _announce
    _announce = fn


def _store(vault: Path) -> Path:
    return Path(vault) / "Jarvis" / "reminders.json"


def _load(vault: Path) -> list[dict]:
    try:
        return json.loads(_store(vault).read_text())
    except Exception:  # noqa: BLE001
        return []


def _save(vault: Path, items: list[dict]) -> None:
    p = _store(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, indent=2))


def add(vault: Path, due_iso: str, text: str) -> dict:
    """Add a reminder. `due_iso` is an ISO-8601 datetime (with or without tz)."""
    items = _load(vault)
    rid = (max((r["id"] for r in items), default=0) + 1) if items else 1
    item = {"id": rid, "due": due_iso, "text": text, "fired": False}
    items.append(item)
    _save(vault, items)
    return item


def list_pending(vault: Path) -> list[dict]:
    return [r for r in _load(vault) if not r.get("fired")]


def cancel(vault: Path, rid: int) -> bool:
    items = _load(vault)
    kept = [r for r in items if r["id"] != rid]
    if len(kept) == len(items):
        return False
    _save(vault, kept)
    return True


def _due(item: dict, now: datetime) -> bool:
    try:
        due = datetime.fromisoformat(item["due"])
    except Exception:  # noqa: BLE001
        return False
    if due.tzinfo is None:
        now = now.replace(tzinfo=None)  # compare naive-to-naive
    else:
        now = now.astimezone(due.tzinfo)
    return now >= due


def _fire(text: str) -> None:
    msg = f"Reminder: {text}"
    try:
        subprocess.run(["notify-send", "-u", "critical", "Jarvis", msg], timeout=5)
    except Exception:  # noqa: BLE001
        pass
    if _announce is not None:
        try:
            _announce(msg)
        except Exception:  # noqa: BLE001
            pass


def check_now(vault: Path) -> list[str]:
    """Fire any due reminders once; returns the texts fired."""
    items = _load(vault)
    now = datetime.now().astimezone()
    fired: list[str] = []
    changed = False
    for r in items:
        if not r.get("fired") and _due(r, now):
            r["fired"] = True
            changed = True
            fired.append(r["text"])
            _fire(r["text"])
    if changed:
        _save(vault, items)
    return fired


async def run_checker(vault: Path, interval_s: int = 30) -> None:
    """Background loop: fire due reminders forever."""
    while True:
        try:
            check_now(vault)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(interval_s)
