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

from . import embeddings
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

    results: list[tuple[str, str]] = []
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
        results.append((rel, snippet))
        if len(results) >= max_files:
            break
    return results


def recall(query: str, vault, k: int = 5) -> str:
    vault = Path(vault)
    query = (query or "").strip()
    if not query:
        return "Empty query."

    sections: list[str] = []

    # Semantic (only if Ollama is up)
    if embeddings.available():
        index = VaultIndex(vault)
        if not index.entries:
            index.build()
        matches = index.search(embeddings.embed(query), k=k)
        if matches:
            sections.append("Semantic matches:")
            for path, chunk, score in matches:
                sections.append(f"[{path}] (score {score:.2f})\n{chunk[:600]}")

    # Keyword
    hits = _keyword_matches(query, vault)
    if hits:
        lines = [f"- {path}" + (f" — {snip}" if snip else "") for path, snip in hits]
        sections.append("\nKeyword matches:\n" + "\n".join(lines))

    if not sections:
        return f"No matches for '{query}' in the vault."
    return "\n".join(sections)
