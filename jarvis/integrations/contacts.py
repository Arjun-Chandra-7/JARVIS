"""Durable people/number memory, stored in the Obsidian vault (git-versioned).

The WhatsApp bridge only knows contacts it has *seen*. When Arjun tells Jarvis "Pradhuman's
number is +91…", that has to be remembered forever — so we persist it here, in the vault, both
as machine-readable JSON (`contacts.json`) and as a human-readable `People/<Name>.md` note that
shows up in Obsidian. Name resolution consults this store FIRST, before the bridge address book.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import CONFIG


def _store() -> Path:
    return CONFIG.vault_path / "contacts.json"


def _load() -> dict:
    try:
        return json.loads(_store().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    p = _store()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_number(raw: str) -> str:
    """Keep leading + and digits only."""
    raw = (raw or "").strip()
    plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    return ("+" if plus else "") + digits


def _key(name: str) -> str:
    return (name or "").strip().lower()


def remember(name: str, number: str = "", note: str = "") -> dict:
    """Store/update a person. Returns {ok, message}."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "message": "Who should I remember? I need a name."}
    number = _norm_number(number)
    data = _load()
    entry = data.get(_key(name), {"name": name})
    entry["name"] = name
    if number:
        entry["number"] = number
    if note:
        entry["note"] = note
    data[_key(name)] = entry
    _save(data)
    _write_note(entry)
    bits = [f"Got it — I'll remember {name}"]
    if number:
        bits.append(f"({number})")
    return {"ok": True, "message": " ".join(bits) + "."}


def _write_note(entry: dict) -> None:
    """A friendly Obsidian note so it's visible + searchable in the vault."""
    try:
        folder = CONFIG.vault_path / "People"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\- ]", "", entry["name"]).strip() or "unknown"
        body = [f"# {entry['name']}", ""]
        if entry.get("number"):
            body.append(f"- **Number:** {entry['number']}")
        if entry.get("note"):
            body.append(f"- **Notes:** {entry['note']}")
        body.append("")
        body.append("*(remembered by Jarvis)*")
        (folder / f"{safe}.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def lookup(name: str) -> dict | None:
    """Exact, then fuzzy (substring / token) match against the local store."""
    data = _load()
    k = _key(name)
    if k in data:
        return data[k]
    for key, entry in data.items():
        if k and (k in key or key in k):
            return entry
    toks = set(k.split())
    for key, entry in data.items():
        if toks & set(key.split()):
            return entry
    return None


def all_contacts() -> list[dict]:
    return list(_load().values())
