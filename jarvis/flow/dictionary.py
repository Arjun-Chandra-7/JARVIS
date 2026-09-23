"""The person's own words: names, projects, slang, and how they are spelled.

Kept in ``$JARVIS_STATE_DIR/dictation-dictionary.json`` (mode 0600), editable by hand:

    {"entries": [{"written": "Viralyst", "spoken": ["viralist", "viral list"], "language": "en",
                  "pronunciation": "vai-ruh-list", "context": "project", "confidence": 1.0}]}

Used twice: as vocabulary for the recogniser, so it hears the word, and as replacements
afterwards, so it is spelled right when it does not. Changes said aloud ("always spell this as
Viralyst", "add this name to my dictionary", "forget that correction") are proposed, not made:
they wait for a confirmation before anything is written.

Seeded only with words that are safe to ship — the product's own vocabulary — never contacts.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

SEED = [
    {"written": "JARVIS", "spoken": ["jarvis", "jar vis"], "language": "en", "context": "assistant"},
    {"written": "Viralyst", "spoken": ["viralist", "viral list", "virallist", "viralest"], "language": "en",
     "context": "project"},
    {"written": "NCERT", "spoken": ["n c e r t", "ncrt", "n.c.e.r.t", "en cert"], "language": "en",
     "context": "school"},
    {"written": "Pythagoras", "spoken": ["pythagorus", "pythagorous", "pitagoras"], "language": "en",
     "context": "school"},
    {"written": "hypotenuse", "spoken": ["hypotenus", "hypotneuse"], "language": "en", "context": "school"},
    {"written": "WhatsApp", "spoken": ["whats app", "what's app", "whatsapp"], "language": "en", "context": "app"},
    {"written": "VS Code", "spoken": ["vs code", "vscode", "v s code"], "language": "en", "context": "app"},
    {"written": "GitHub", "spoken": ["git hub", "github"], "language": "en", "context": "app"},
]


@dataclass
class Entry:
    written: str
    spoken: list[str] = field(default_factory=list)
    language: str = "en"
    pronunciation: str = ""
    context: str = ""
    confidence: float = 1.0


def _path() -> Path:
    return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser() / "dictation-dictionary.json"


def load() -> list[Entry]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        rows = raw.get("entries", []) if isinstance(raw, dict) else []
    except (OSError, ValueError):
        rows = SEED
    out = []
    for row in rows:
        if isinstance(row, dict) and row.get("written"):
            out.append(Entry(**{k: v for k, v in row.items() if k in Entry.__dataclass_fields__}))
    return out


def save(entries: list[Entry]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"entries": [asdict(e) for e in entries]}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def replacements(entries: Optional[list[Entry]] = None) -> list[tuple[str, str]]:
    """(spoken, written) pairs, longest first so "viral list" wins over "viral"."""
    pairs = []
    for e in entries if entries is not None else load():
        for s in e.spoken:
            if s and s.lower() != e.written.lower():
                pairs.append((s, e.written))
        # The written form in the wrong case is also a spelling to correct: "ncert" → "NCERT".
        if e.written.lower() != e.written or e.written.upper() == e.written:
            pairs.append((e.written.lower(), e.written))
    return sorted(pairs, key=lambda p: -len(p[0]))


def vocabulary(entries: Optional[list[Entry]] = None, limit: int = 40) -> str:
    """Words to prime the recogniser with."""
    return ", ".join(e.written for e in (entries if entries is not None else load())[:limit])


def closest_word(text: str, written: str) -> Optional[str]:
    """In "always spell this as Viralyst" after dictating "viralist is live", which word was it."""
    words = re.findall(r"[\w'ऀ-ॿ]+", text or "")
    best = difflib.get_close_matches(written.lower(), [w.lower() for w in words], n=1, cutoff=0.55)
    if not best:
        return None
    for w in words:
        if w.lower() == best[0]:
            return w
    return None


def with_spelling(entries: list[Entry], written: str, spoken: str) -> list[Entry]:
    """The dictionary with ``spoken`` now written as ``written`` (not saved)."""
    out = [Entry(**asdict(e)) for e in entries]
    for e in out:
        if e.written.lower() == written.lower():
            if spoken and spoken.lower() not in [s.lower() for s in e.spoken]:
                e.spoken.append(spoken)
            return out
    out.append(Entry(written=written, spoken=[spoken] if spoken and spoken.lower() != written.lower() else [],
                     context="added by voice", confidence=0.9))
    return out


def without_last_added(entries: list[Entry]) -> list[Entry]:
    added = [i for i, e in enumerate(entries) if e.context == "added by voice"]
    return [e for i, e in enumerate(entries) if not added or i != added[-1]]
