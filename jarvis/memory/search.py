"""Recall over everything Jarvis knows — vault notes, past conversation, and distilled facts.

This used to run two searches that never met: a semantic pass over a JSON vector index, and a
separate `rg` keyword pass whose hits were printed underneath as a second list. A note that both
signals liked weakly still lost to a note only one of them liked, because nothing ever compared
them. `memory/store.py` now holds both indexes in one SQLite file and fuses their rankings, so this
module is mostly about deciding what to search and how to phrase the answer for a language model.

The store is kept in step lazily: if the vault has changed since the last sync we re-index the
handful of touched files first, which keeps `recall` honest without anyone remembering to run
`--index`.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import embeddings
from .store import MemoryStore, get_store

# Re-sync at most this often; a vault sweep is cheap but not free.
_SYNC_INTERVAL_S = 120.0
_last_sync: dict[str, float] = {}


def _embedder(model: str = "nomic-embed-text"):
    """An embed function, or None when Ollama can't actually produce vectors.

    `embeddings.available()` only proves the Ollama server answers — it says nothing about the
    embedding model being pulled. Asking for one vector settles it, and a wrong answer here is
    what previously left semantic recall silently dead while reporting itself healthy.
    """
    if not embeddings.available(timeout=1.0):
        return None
    probe = embeddings.embed("ping", model, timeout=20.0)
    if not probe:
        return None
    return lambda text: embeddings.embed(text, model, timeout=30.0)


def _newest_mtime(vault: Path) -> float:
    newest = 0.0
    for f in vault.rglob("*.md"):
        if {".jarvis", ".git", "private"} & set(f.parts):
            continue
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            continue
    return newest


def sync(vault: Path, store: Optional[MemoryStore] = None, force: bool = False) -> dict:
    """Fold vault changes into the store. Cheap and idempotent; safe to call before every recall."""
    vault = Path(vault)
    store = store or get_store(vault)
    key = str(vault)
    now = time.monotonic()
    if not force and now - _last_sync.get(key, 0.0) < _SYNC_INTERVAL_S:
        return {"skipped": True}
    _last_sync[key] = now
    return store.sync_vault(vault, embed=_embedder())


def _when(ts: float) -> str:
    try:
        dt = datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return ""
    days = (datetime.now() - dt).days
    if days <= 0:
        return f"today {dt:%H:%M}"
    if days == 1:
        return f"yesterday {dt:%H:%M}"
    if days < 7:
        return f"{dt:%A} {dt:%H:%M}"
    return f"{dt:%d %b %Y}"


def recall(query: str, vault, k: int = 6) -> str:
    """Search memory and render the hits for a language model to read."""
    vault = Path(vault)
    query = (query or "").strip()
    if not query:
        return "Empty query."

    store = get_store(vault)
    try:
        sync(vault, store)
    except Exception:  # noqa: BLE001 - a stale index still beats no answer
        pass

    hits = store.search(query, k=k, embed=_embedder())
    if not hits:
        return f"No matches for '{query}' in memory."

    lines: list[str] = []
    for h in hits:
        if h["kind"] == "episode":
            head = f"[said {_when(h['ts'])} by {h['ref']}]"
        elif h["kind"] == "fact":
            head = "[remembered fact]"
        else:
            head = f"[{h['ref']}]"
        body = h["text"].strip()
        lines.append(f"{head}\n{body[:700]}")
    return "\n\n".join(lines)


def remember(text: str, vault, ref: str = "profile") -> str:
    """Store a durable fact. Used by the agent when the user states something worth keeping."""
    store = get_store(Path(vault))
    store.add_fact(text, ref=ref, embed=_embedder())
    return f"Remembered: {text[:160]}"


def record_turn(who: str, text: str, vault) -> None:
    """Append one conversational turn to episodic memory. Best effort — never raises upward."""
    try:
        get_store(Path(vault)).add_episode(who, text, embed=_embedder())
    except Exception:  # noqa: BLE001
        pass
