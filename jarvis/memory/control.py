"""Memory you can inspect and change.

`recall()` returns a blob of text for the model to read. That is fine for the model and useless
for a person: you cannot see where a fact came from, when it was recorded, whether you told JARVIS
or it inferred it, or correct it when it is wrong.

This returns the same vault content as structured records with provenance, and provides the two
operations that make memory trustworthy rather than merely persistent:

* **correct** — replace a wrong line, keeping the original in the note's history so the change is
  auditable rather than silent.
* **forget** — remove a line from active recall.

Both write through the vault's git auto-commit, so every change is recorded. "Forget" means
removed from the notes JARVIS reads; it does **not** rewrite git history, and this module says so
rather than implying the data is gone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import vault as vaultmod

SNIPPET_CHARS = 220

# Notes JARVIS wrote about itself vs. things the user stated. Provenance is a real distinction:
# "Arjun lives in Bangalore" that the user typed is stronger evidence than one JARVIS inferred.
_EXPLICIT_DIRS = ("profile", "people", "contacts")
_INFERRED_DIRS = ("Jarvis", "journal", "tasks", "logs")


@dataclass
class Memory:
    path: str
    title: str
    snippet: str
    kind: str = "note"          # explicit | inferred | note
    source: str = ""
    recorded: str = ""
    line: int = 0
    score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "path": self.path, "title": self.title, "snippet": self.snippet,
            "kind": self.kind, "source": self.source, "recorded": self.recorded,
            "line": self.line, "score": round(self.score, 3),
        }


def _classify(rel_path: str) -> str:
    head = rel_path.split("/", 1)[0].lower()
    if head in (d.lower() for d in _EXPLICIT_DIRS) or rel_path.lower().startswith("profile"):
        return "explicit"
    if head in (d.lower() for d in _INFERRED_DIRS):
        return "inferred"
    return "note"


def _recorded_at(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def _iter_notes(vault: Path):
    private = vault / "private"
    for f in sorted(vault.rglob("*.md")):
        # `private/` holds imported chat history and away-mode records. It is excluded from
        # ordinary recall by design, so it stays excluded here too.
        try:
            f.relative_to(private)
            continue
        except ValueError:
            pass
        if ".git" in f.parts:
            continue
        yield f


def search(vault, query: str, limit: int = 20) -> list[Memory]:
    """Find lines in the vault matching `query`, with where and when they came from."""
    vault = Path(vault)
    query = (query or "").strip()
    if not query or not vault.is_dir():
        return []

    terms = [t.lower() for t in re.findall(r"[\w']+", query) if len(t) > 1]
    if not terms:
        return []

    out: list[Memory] = []
    for f in _iter_notes(vault):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = str(f.relative_to(vault))
        recorded = _recorded_at(f)
        kind = _classify(rel)
        for n, raw in enumerate(lines, 1):
            line = raw.strip()
            if len(line) < 3 or line.startswith("#"):
                continue
            low = line.lower()
            hits = sum(1 for t in terms if t in low)
            if not hits:
                continue
            out.append(Memory(
                path=rel,
                title=f.stem.replace("-", " ").replace("_", " "),
                snippet=line[:SNIPPET_CHARS],
                kind=kind,
                source=f"{rel}:{n}",
                recorded=recorded,
                line=n,
                score=hits / len(terms),
            ))

    # Strongest match first; explicit statements outrank things JARVIS inferred at equal score.
    out.sort(key=lambda m: (-m.score, m.kind != "explicit", m.path))
    return out[:limit]


def _locate(vault: Path, path: str, snippet: str) -> tuple[Optional[Path], int, list[str]]:
    """Find the exact line to change. Returns (file, index, lines) or (None, -1, [])."""
    target = (vault / path).resolve()
    try:
        target.relative_to(vault.resolve())    # refuse to write outside the vault
    except ValueError:
        return None, -1, []
    if not target.is_file():
        return None, -1, []
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    needle = (snippet or "").strip()
    for i, raw in enumerate(lines):
        if needle and needle[:SNIPPET_CHARS] in raw:
            return target, i, lines
    return None, -1, lines


def correct(vault, path: str, snippet: str, replacement: str) -> dict:
    """Replace a remembered line. The old text is kept in the note so the change is auditable."""
    vault = Path(vault)
    replacement = (replacement or "").strip()
    if not replacement:
        return {"ok": False, "message": "A correction needs replacement text."}

    target, idx, lines = _locate(vault, path, snippet)
    if target is None:
        return {"ok": False, "message": "That line is no longer in the note — it may have changed."}

    original = lines[idx].strip()
    stamp = datetime.now().strftime("%Y-%m-%d")
    lines[idx] = replacement
    # An HTML comment: invisible in Obsidian's reader, still there when you look at the source.
    lines.insert(idx + 1, f"<!-- corrected {stamp}: was \"{original}\" -->")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    vaultmod.git_autocommit(vault, f"jarvis: corrected memory in {path}")
    return {"ok": True, "message": "Corrected. JARVIS will use the new version from now on."}


def forget(vault, path: str, snippet: str) -> dict:
    """Remove a line from active recall.

    Honest about what this does: the line is removed from the note, so JARVIS stops reading it.
    The vault is a git repository, so the previous version remains in its history.
    """
    vault = Path(vault)
    target, idx, lines = _locate(vault, path, snippet)
    if target is None:
        return {"ok": False, "message": "That line is no longer in the note."}

    del lines[idx]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    vaultmod.git_autocommit(vault, f"jarvis: forgot a line in {path}")
    return {
        "ok": True,
        "message": "Removed from recall. It remains in the vault's git history.",
    }
