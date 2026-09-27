"""Exports of the student's own study material: Markdown and JSON.

Revision notes, formula sheets, mistake summaries, flashcards, quiz results and study plans.
What goes in is what the companion produced (cards written for this project, the student's
progress, plans) — never pages of a textbook. A source excerpt is at most ``sources.MAX_EXCERPT``
characters with its citation, and the student's answer text is included only when they ask
(``include_answers``). Files are written 0600 into a directory the caller names; nothing is
written by default and nothing lands inside the repository.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from .curriculum import MISCONCEPTIONS
from .knowledge import Card
from .mastery import DISCLAIMER, MasteryStore
from .revision import RevisionPlan
from .science_engine import FORMULAE
from .types import Language


def revision_notes(cards: Iterable[Card], lang: Language = Language.ENGLISH) -> str:
    out = ["# Revision notes", "", "_NCERT-style notes written by Jarvis — check against your textbook._", ""]
    for c in cards:
        out.append(f"## {c.title}")
        out += [f"- {x}" for x in c.revision]
        if c.mistakes:
            out.append("")
            out.append("**Avoid:** " + "; ".join(c.mistakes))
        out.append("")
    return "\n".join(out).strip() + "\n"


def formula_sheet(formula_ids: Iterable[str]) -> str:
    out = ["# Formula sheet", ""]
    for fid in dict.fromkeys(formula_ids):
        f = FORMULAE.get(fid)
        if not f:
            continue
        syms = ", ".join(f"{s} = {q} ({u or 'no unit'})" for s, (q, u) in f.symbols.items())
        out.append(f"- **{f.name}**: `{f.equation}` — {syms}" + (f". {f.note}" if f.note else ""))
    return "\n".join(out) + "\n"


def mistake_summary(mastery: MasteryStore) -> str:
    out = ["# Mistakes to fix", "", f"_{DISCLAIMER}_", ""]
    recs = sorted(mastery.misconceptions.values(), key=lambda m: -m.count)
    if not recs:
        out.append("No recorded mistakes.")
    for m in recs:
        mc = MISCONCEPTIONS[m.id]
        out.append(f"- **{mc.label}** ({m.count}×{', fixed' if m.resolved else ''}) — {mc.correction}")
    return "\n".join(out) + "\n"


def flashcards(cards: Iterable[Card], lang: Language = Language.ENGLISH) -> list[dict]:
    deck = []
    for c in cards:
        chk = c.check.get(lang) or c.check[Language.ENGLISH]
        deck.append({"front": chk.question, "back": chk.answer, "topic": c.topic})
        for p in c.points:
            if p.required:
                deck.append({"front": f"{c.title}: key point?", "back": p.idea, "topic": c.topic})
    return deck


def quiz_results(summary: dict) -> dict:
    return {"note": DISCLAIMER, **summary}


def study_plan(plan: RevisionPlan) -> str:
    return "# Study plan\n\n" + "\n".join(f"- {line}" for line in plan.text().splitlines()) + \
           "\n\n_No calendar events were created._\n"


def write(directory: Path, name: str, content, *, repo_root: Optional[Path] = None) -> Path:
    """Write one export. Refuses to write inside the repository (exports are personal data)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")[:60] or "export"
    directory = directory.expanduser().resolve()
    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    if directory == root or root in directory.parents:
        raise ValueError("refusing_to_export_into_repository")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path
