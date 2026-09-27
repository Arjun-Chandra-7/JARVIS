"""Marks-based schemes, and checking a student's answer against one.

A ``MarkScheme`` is a list of scoring points, each an idea with keyword alternatives. Where it
came from matters and is carried along:

* from a real marking scheme the student supplied → ``official=True`` and a citation;
* from a verified topic card → a *general* scheme: marks are estimated "against an NCERT-style
  rubric", and the note says it is not the board's;
* from nothing → no marks at all: "cannot grade reliably without a marking scheme".

Checking is generous about wording (any keyword group counts, stems match) and strict about
facts (a misconception signal on a line marks that line inaccurate). Words the OCR was unsure of
are never counted against the student: if an uncertain word could be the missing keyword, the
point is "uncertain", the band widens, and the student is asked about that word only if it would
change the result.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional, Sequence

from .curriculum import MISCONCEPTIONS
from .knowledge import Card
from .types import Evaluation, Finding, MarkScheme, ScoringPoint

_NORM = re.compile(r"[^\wऀ-ॿ√²³+−\-=/.]+")


def _norm(text: str) -> str:
    t = (text or "").lower().replace("^2", "²").replace("**2", "²").replace("-", " ").replace("−", " − ")
    return " " + _NORM.sub(" ", t) + " "


def _has(text: str, kw: str) -> bool:
    k = _norm(kw).strip()
    if not k:
        return False
    if len(k) <= 2 and not k.isalpha():
        return k in text
    return (" " + k) in text or (len(k) >= 5 and k[:-1] in text)   # "ions"/"ion", "moves"/"move", stems


def scheme_from_card(card: Card, marks: int) -> MarkScheme:
    """The general scheme for a marks value, from the card's marks-based answer."""
    best = max((m for m in card.exam if m <= marks), default=None)
    ids = [pid for pid, _ in card.exam[best]] if best else [p.id for p in card.points if p.required]
    chosen = [card.point(pid) for pid in ids if card.point(pid)]
    per = marks / max(1, len(chosen))
    pts = [ScoringPoint(p.id, p.idea, p.keywords, round(per * 2) / 2, True, p.kind) for p in chosen]
    optional = [p for p in card.points if p.id not in ids]
    return MarkScheme(total=marks, points=pts, optional=optional, formulae=list(card.formulae),
                      diagram_required=any(p.kind == "diagram" and p.required for p in pts),
                      max_words=max(25, 30 * marks), official=False)


def point_status(point: ScoringPoint, text: str, uncertain: Sequence[str] = ()) -> str:
    """"present", "uncertain" (only an OCR-uncertain word stands between it and present), "missing"."""
    t = _norm(text)
    for group in point.keywords:
        if all(_has(t, k) for k in group):
            return "present"
    if uncertain:
        for group in point.keywords:
            lacking = [k for k in group if not _has(t, k)]
            if lacking and all(any(difflib.SequenceMatcher(None, k.lower(), u.lower()).ratio() >= 0.6
                                   for u in uncertain) for k in lacking):
                return "uncertain"
    return "missing"


def _line_of(lines: Sequence[str], point: ScoringPoint) -> Optional[int]:
    for i, ln in enumerate(lines, start=1):
        if point_status(point, ln) == "present":
            return i
    return None


def misconceptions_in(lines: Sequence[str], candidates: Sequence[str]) -> list[tuple[str, int]]:
    out = []
    for mid in candidates:
        m = MISCONCEPTIONS.get(mid)
        if not m:
            continue
        for i, ln in enumerate(lines, start=1):
            if any(re.search(rx, ln, re.I) for rx in m.signals):
                out.append((mid, i))
                break
    return out


def _band(low: float, high: float, total: int) -> str:
    f = lambda x: (str(int(x)) if float(x).is_integer() else f"{x:g}")  # noqa: E731
    if low == high:
        return f"likely {f(low)}/{total}"
    return f"approximately {f(low)}–{f(high)} marks (out of {total})"


def evaluate(answer: str, scheme: Optional[MarkScheme], *, misconception_ids: Sequence[str] = (),
             uncertain_words: Sequence[str] = (), card: Optional[Card] = None) -> Evaluation:
    lines = [ln for ln in re.split(r"\n+|(?<=[.;])\s+(?=[A-Z0-9(])", (answer or "").strip()) if ln.strip()]
    ev = Evaluation(uncertain_words=list(uncertain_words))
    if not lines:
        ev.missing.append("No answer was given.")
        ev.marks_estimate = "cannot grade an empty answer"
        return ev

    # Facts first: a known misconception on a line is an inaccuracy on that line.
    for mid, ln in misconceptions_in(lines, misconception_ids):
        m = MISCONCEPTIONS[mid]
        ev.inaccurate.append(f"Line {ln}: {m.label.lower()}. {m.correction}")
        ev.findings.append(Finding("factual", "inaccurate", m.correction, ln))
        ev.wrong_line = ev.wrong_line or ln

    if scheme is None:
        ev.marks_estimate = "cannot grade reliably without a marking scheme"
        ev.confidence = 0.4
        return ev

    got = low = high = 0.0
    for p in scheme.points:
        status = point_status(p, answer, uncertain_words)
        if status == "present":
            ev.correct.append(p.idea)
            ev.findings.append(Finding("completeness", "correct", p.idea, _line_of(lines, p)))
            got += p.marks
        elif status == "uncertain":
            ev.findings.append(Finding("completeness", "uncertain",
                                       f"{p.idea} — probably present, but a word was hard to read", None))
            high += p.marks
        else:
            ev.missing.append(p.idea)
            ev.findings.append(Finding("completeness", "missing", p.idea))
    # An inaccurate line costs the point it was trying to make, at most one per inaccuracy.
    penalty = min(got, sum(1 for f in ev.findings if f.verdict == "inaccurate") * max(p.marks for p in scheme.points))
    low, high = got - penalty, got - penalty + high
    low, high = max(0.0, round(low * 2) / 2), min(float(scheme.total), round(high * 2) / 2)
    ev.graded = True
    rubric = "" if scheme.official else " against an NCERT-style rubric, not an official marking scheme"
    ev.marks_estimate = _band(low, high, scheme.total) + rubric

    if scheme.total >= 3 and len(lines) == 1 and len(answer) > 160:
        ev.findings.append(Finding("presentation", "missing", "Split the answer into separate points for each mark."))
    if card is not None:
        ev.corrected = "\n".join(f"{i}. {s}" for i, s in enumerate(card.exam_answer(scheme.total), start=1))
    if ev.inaccurate:
        ev.next_step = "Fix the inaccurate line first: " + ev.inaccurate[0].split(": ", 1)[-1]
    elif ev.missing:
        ev.next_step = "Add this point: " + ev.missing[0]
    else:
        ev.next_step = "Complete — practise writing it in the same order from memory."
    uncertain_hits = [f for f in ev.findings if f.verdict == "uncertain"]
    if uncertain_hits:
        ev.clarify = ("I couldn't read " + ", ".join(f"“{w}”" for w in uncertain_words[:3]) +
                      " clearly — can you confirm what you wrote there?")
    ev.confidence = round(0.9 - 0.15 * len(uncertain_hits) - (0.1 if not scheme.official else 0), 2)
    return ev
