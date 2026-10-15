"""Persistent reminders — survive restarts (unlike in-memory timers).

Stored as JSON in the vault (Jarvis/reminders.json). A background checker fires due reminders with a
desktop notification and, in voice mode, a spoken alert. Set the announcer to speak; it always
notifies via notify-send regardless.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from datetime import datetime, timedelta
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


_ZONE_WORDS = re.compile(r"\s*\b(?:IST|UTC|GMT|Z|local(?:\s+time)?|hrs?|hours)\b\.?\s*$", re.I)
_ZONE_OFFSETS = {"ist": "+05:30", "utc": "+00:00", "gmt": "+00:00", "z": "+00:00"}


def parse_due(raw: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """A reminder time as the model writes it, to an aware datetime; None when it isn't one.

    `datetime.fromisoformat` alone was used, and the model writes "2026-10-15 21:35 IST": the
    reminder was saved, reported as set, and never fired, because every check failed to parse it
    and the failure was swallowed.
    """
    text = (raw or "").strip()
    if not text:
        return None
    now = now or datetime.now().astimezone()
    zone = _ZONE_WORDS.search(text)
    offset = _ZONE_OFFSETS.get(zone.group(0).strip(" .").lower(), "") if zone else ""
    core = _ZONE_WORDS.sub("", text).strip().replace("T", " ", 1) if zone else text.replace("T", " ", 1)
    for candidate in (text, core, f"{core}{offset}" if offset else None):
        if not candidate:
            continue
        try:
            when = datetime.fromisoformat(candidate)
            return when if when.tzinfo else when.replace(tzinfo=now.tzinfo)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M %p", "%d %B %Y %H:%M", "%d %b %Y %H:%M"):
        try:
            when = datetime.strptime(core, fmt)
            return when.replace(tzinfo=now.tzinfo)
        except ValueError:
            pass
    # Just a clock time: today, or tomorrow when it has already passed.
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            t = datetime.strptime(core.upper(), fmt).time()
        except ValueError:
            continue
        when = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        return when if when > now else when + timedelta(days=1)
    return None


def add(vault: Path, due_iso: str, text: str) -> dict:
    """Add a reminder. `due_iso` is a time the model wrote; stored as an ISO-8601 datetime.

    Raises ValueError when the time cannot be read, so the tool says so instead of "set".
    """
    when = parse_due(due_iso)
    if when is None:
        raise ValueError(f"couldn't read the reminder time {due_iso!r}")
    items = _load(vault)
    rid = (max((r["id"] for r in items), default=0) + 1) if items else 1
    item = {"id": rid, "due": when.isoformat(timespec="minutes"), "text": text, "fired": False}
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
    due = parse_due(str(item.get("due", "")), now.astimezone() if now.tzinfo else None)
    if due is None:
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
            # Late — Jarvis was off, or the time could not be read until now: say when it was
            # for, so "check the oven" two hours on is not taken as about now.
            due = parse_due(str(r.get("due", "")), now)
            late = due is not None and (now - due.astimezone(now.tzinfo)) > timedelta(minutes=10)
            _fire(f"{r['text']} (missed — it was due at {due.astimezone(now.tzinfo):%H:%M})" if late else r["text"])
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
