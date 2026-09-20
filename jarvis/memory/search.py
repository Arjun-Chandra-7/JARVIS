"""Hybrid recall over the vault: semantic (Ollama, if available) + keyword, merged.

Keyword search uses a real `rg` binary when one is on PATH, otherwise a dependency-free Python
walk (note: Claude Code ships `rg` as a shell function, which Python can't see — hence the
fallback).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import embeddings, fuse
from .index import VaultIndex


def _terms(query: str) -> list[str]:
    return [t for t in re.findall(r"\w+", query) if len(t) > 2][:6]


def _rg_files(pattern: str, vault: Path, max_files: int) -> list[str]:
    rg = shutil.which("rg")
    if not rg:
        return []
    try:
        out = subprocess.run(
            [rg, "-i", "-l", "-g", "*.md", "-g", "!Jarvis/private/**", pattern, str(vault)],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    return [line for line in out.splitlines() if line.strip()][:max_files]


def _keyword_matches(query: str, vault: Path, max_files: int = 8) -> list[tuple[str, str]]:
    terms = [t.lower() for t in _terms(query)]
    if not terms:
        return []

    # Prefer a real rg binary if present (fast on large vaults).
    rg_hits = _rg_files("|".join(re.escape(t) for t in terms), vault, max_files)
    files = [Path(f) for f in rg_hits] if rg_hits else [
        f for f in vault.rglob("*.md") if ".git" not in f.parts and ".jarvis" not in f.parts and "private" not in f.parts
    ]

    found: list[tuple[str, str, str]] = []          # (path, snippet, full text)
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        if not any(t in text.lower() for t in terms):
            continue
        snippet = ""
        for line in text.splitlines():
            if any(t in line.lower() for t in terms):
                snippet = line.strip()[:200]
                break
        try:
            rel = str(f.relative_to(vault))
        except ValueError:
            rel = str(f)
        found.append((rel, snippet, text))

    if not found:
        return []
    # Ranked, not just filtered. Before this the order was whatever rg listed first, so a note
    # mentioning a term once outranked the note that was about it purely by filename.
    scores = fuse.bm25_scores(query, [text for _rel, _snip, text in found])
    ordered = sorted(zip(found, scores), key=lambda pair: -pair[1])
    return [(rel, snippet) for (rel, snippet, _text), _score in ordered[:max_files]]


def recall(query: str, vault, k: int = 5) -> str:
    """One ranked answer, not two lists under two headings.

    Both halves run — embeddings for paraphrase, keywords for the proper nouns embeddings are bad
    at — and then reciprocal rank fusion merges them by position rather than by score, because a
    cosine and a BM25 score are not comparable numbers. What comes back is ordered by how much
    the two halves agreed.
    """
    vault = Path(vault)
    query = (query or "").strip()
    if not query:
        return "Empty query."

    # path -> the best text seen for it, from whichever half found it
    excerpt: dict[str, str] = {}
    semantic_order: list[str] = []
    keyword_order: list[str] = []

    if embeddings.available():
        index = VaultIndex(vault)
        if not index.entries:
            index.build()
        for path, chunk, _score in index.search(embeddings.embed(query), k=k * 2):
            if path not in excerpt:
                excerpt[path] = chunk[:600]
                semantic_order.append(path)

    for path, snippet in _keyword_matches(query, vault):
        keyword_order.append(path)
        excerpt.setdefault(path, snippet)

    if not semantic_order and not keyword_order:
        return f"No matches for '{query}' in the vault."

    both = set(semantic_order) & set(keyword_order)
    lines: list[str] = []
    for path in fuse.fuse(semantic_order, keyword_order)[:k]:
        # Worth saying which results both halves found: that is the signal the fusion is built
        # on, and it tells the reader why this one is at the top.
        mark = " (both)" if path in both else ""
        text = (excerpt.get(path) or "").strip()
        lines.append(f"[{path}]{mark}\n{text}" if text else f"[{path}]{mark}")
    return "\n\n".join(lines)
