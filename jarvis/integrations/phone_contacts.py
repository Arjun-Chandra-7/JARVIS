"""Read the phone's address book from KDE Connect's synced vCards.

KDE Connect mirrors the phone's contacts to ~/.local/share/kpeoplevcard/<device>/*.vcf. That's the
real, complete address book (thousands of names + numbers) — far better than what WhatsApp exposes.
We parse it (cached) so Jarvis can resolve "message <name>" to a real number.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

_BASE = Path("~/.local/share/kpeoplevcard").expanduser()
_cache: dict = {"sig": None, "contacts": []}


def _dirs() -> list[Path]:
    if not _BASE.exists():
        return []
    return [d for d in _BASE.iterdir() if d.is_dir()]


def _norm_number(raw: str) -> str:
    raw = raw.strip()
    plus = "+" in raw
    digits = re.sub(r"\D", "", raw)
    return ("+" if plus else "") + digits


def _parse_vcf(text: str) -> tuple[str, str] | None:
    name, number = "", ""
    for line in text.splitlines():
        u = line.upper()
        if u.startswith("FN:") and not name:
            name = line.split(":", 1)[1].strip()
        elif u.startswith("TEL") and ":" in line and not number:
            number = _norm_number(line.split(":", 1)[1])
    if name and number and len(re.sub(r"\D", "", number)) >= 7:
        return name, number
    return None


def _signature() -> tuple:
    dirs = _dirs()
    n = sum(1 for d in dirs for _ in d.glob("*.vcf"))
    mtime = max((d.stat().st_mtime for d in dirs), default=0)
    return (n, round(mtime))


def all_contacts() -> list[dict]:
    """[{name, number}] for every phone contact, cached until the vCard folder changes."""
    sig = _signature()
    if _cache["sig"] == sig and _cache["contacts"]:
        return _cache["contacts"]
    out, seen = [], set()
    for d in _dirs():
        for vcf in d.glob("*.vcf"):
            try:
                parsed = _parse_vcf(vcf.read_text(encoding="utf-8", errors="ignore"))
            except Exception:  # noqa: BLE001
                continue
            if not parsed:
                continue
            name, number = parsed
            key = (name.lower(), number)
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "number": number})
    _cache.update(sig=sig, contacts=out)
    return out


def lookup(name: str) -> list[dict]:
    """Name → matching phone contacts, best first. Exact, then first-name/token, then substring."""
    q = (name or "").strip().lower()
    if not q:
        return []
    contacts = all_contacts()
    exact, token, sub = [], [], []
    qtoks = set(q.split())
    for c in contacts:
        nl = c["name"].lower()
        if nl == q:
            exact.append(c)
        elif qtoks & set(nl.split()):
            token.append(c)
        elif q in nl:
            sub.append(c)
    # de-dupe by number, preserve priority order
    seen, ordered = set(), []
    for c in exact + token + sub:
        if c["number"] in seen:
            continue
        seen.add(c["number"])
        ordered.append(c)
    return ordered[:8]


def count() -> int:
    return len(all_contacts())
