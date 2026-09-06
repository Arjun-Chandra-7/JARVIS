"""A lightweight local vector index over the vault's Markdown notes.

Chunks notes, embeds them (via a pluggable embed function), and does cosine top-k search. Persisted
as JSON under `<vault>/.jarvis/`. Incremental: unchanged files (by mtime) are not re-embedded.
Small enough for a personal vault; swap for LanceDB if it ever gets huge. Pure logic is unit-tested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import embeddings

EmbedFn = Callable[[str], Optional[list]]


def chunk_markdown(text: str, max_chars: int = 1200) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) <= max_chars:
                current = para
            else:
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i : i + max_chars])
                current = ""
    if current:
        chunks.append(current)
    return chunks


class VaultIndex:
    def __init__(self, vault: Path, model: str = "nomic-embed-text") -> None:
        self.vault = Path(vault)
        self.model = model
        store = self.vault / ".jarvis"
        store.mkdir(exist_ok=True)
        self.path = store / "semindex.json"
        self.entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.entries = json.loads(self.path.read_text())
            except Exception:  # noqa: BLE001
                self.entries = []

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.entries))

    def _md_files(self) -> list[Path]:
        return [
            p
            for p in self.vault.rglob("*.md")
            if ".jarvis" not in p.parts and ".git" not in p.parts and "private" not in p.parts
        ]

    def build(self, embed_fn: Optional[EmbedFn] = None, progress: Optional[Callable] = None) -> dict:
        embed_fn = embed_fn or (lambda t: embeddings.embed(t, self.model))
        prev_mtime = {e["path"]: e["mtime"] for e in self.entries}
        by_path: dict[str, list[dict]] = {}
        for entry in self.entries:
            by_path.setdefault(entry["path"], []).append(entry)

        rebuilt: list[dict] = []
        stats = {"files_indexed": 0, "chunks": 0, "unchanged": 0}
        current: set[str] = set()

        for file in self._md_files():
            rel = str(file.relative_to(self.vault))
            current.add(rel)
            mtime = file.stat().st_mtime
            if rel in prev_mtime and abs(prev_mtime[rel] - mtime) < 1e-6:
                rebuilt.extend(by_path[rel])
                stats["unchanged"] += 1
                continue
            stats["files_indexed"] += 1
            text = file.read_text(errors="ignore")
            for chunk in chunk_markdown(text):
                vec = embed_fn(chunk)
                if vec is None:
                    continue
                rebuilt.append({"path": rel, "mtime": mtime, "chunk": chunk, "vec": vec})
                stats["chunks"] += 1
                if progress:
                    progress(rel, stats["chunks"])

        self.entries = [e for e in rebuilt if e["path"] in current]
        self._save()
        return stats

    def search(self, query_vec: Optional[list], k: int = 5) -> list[tuple[str, str, float]]:
        if not self.entries or query_vec is None:
            return []
        matrix = np.asarray([e["vec"] for e in self.entries], dtype=np.float32)
        query = np.asarray(query_vec, dtype=np.float32)
        matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8)
        query_norm = query / (np.linalg.norm(query) + 1e-8)
        sims = matrix_norm @ query_norm
        top = np.argsort(-sims)[:k]
        return [(self.entries[i]["path"], self.entries[i]["chunk"], float(sims[i])) for i in top]
