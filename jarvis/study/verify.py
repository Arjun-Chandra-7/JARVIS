"""The last gate before an academic answer is returned. Deterministic checks only.

* Every citation names a chunk that exists, with the locator that chunk actually has — a page
  number cannot be invented, because it is copied from the chunk, and this re-checks it.
* Every claim marked as coming from the source is supported by the chunk it cites.
* Anything in quotation marks presented as source wording appears verbatim in a cited chunk.
* A response not backed by an official source may not call itself an NCERT answer.
* Model-written text is scanned for the topic's known misconceptions ("current flows the same
  way as electrons") and for missing required scoring points.
* The language matches what was asked; Hinglish is not silently turned into Sanskritised Hindi.

Failures are recorded in ``response.uncertainty`` and ``response.audit``; hard failures (a claim
the source doesn't support, a fabricated quote) also clear ``grounded``.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from . import languages
from .curriculum import MISCONCEPTIONS
from .retrieval import supports
from .sources import SourceStore
from .types import Coverage, Language, MarkScheme, StudyResponse, SupportKind

_QUOTED = re.compile(r"[“\"]([^”\"]{12,})[”\"]")
_EXACT_NCERT = re.compile(r"(?i)\b(?:exact\s+)?ncert\s+(?:answer|text|says)\b|\bas\s+per\s+ncert\b|\bncert\s+answer\b")


def check(resp: StudyResponse, store: Optional[SourceStore] = None, *, language: Optional[Language] = None,
          coverage: Coverage = Coverage.GENERAL, misconception_ids: Iterable[str] = (),
          scheme: Optional[MarkScheme] = None, generated: bool = False) -> list[str]:
    problems: list[str] = []
    chunks = store.chunks if store else {}

    # 1. Citations are real and their locators are the chunk's own.
    for c in resp.citations:
        ch = chunks.get(c.chunk_id)
        if ch is None:
            problems.append("citation_unknown_chunk")
        elif c.locator != ch.locator() or c.doc_id != ch.doc_id:
            problems.append("citation_locator_mismatch")

    # 2. Source claims are supported by what they cite.
    for cl in resp.claims:
        if cl.support is not SupportKind.SOURCE:
            continue
        if not cl.citations:
            problems.append("source_claim_without_citation")
            continue
        if not any(supports(chunks[c.chunk_id], cl.text, 0.5) for c in cl.citations if c.chunk_id in chunks):
            problems.append("source_claim_unsupported")

    # 3. Quoted source wording must be verbatim.
    if resp.citations:
        body = "\n".join(s.body for s in resp.sections)
        cited = " ".join(re.sub(r"\s+", " ", chunks[c.chunk_id].text) for c in resp.citations if c.chunk_id in chunks)
        for q in _QUOTED.findall(body):
            if re.sub(r"\s+", " ", q).strip(" .…") not in cited:
                problems.append("quote_not_in_source")

    # 4. Labels: only an official source can back "NCERT answer".
    all_text = resp.label + " " + " ".join(s.heading + " " + s.body for s in resp.sections)
    if coverage is not Coverage.OFFICIAL_SOURCE and _EXACT_NCERT.search(all_text):
        problems.append("claims_ncert_without_source")

    # 5. Model-written content: no known misconception, every required point present.
    if generated:
        text = " ".join(s.body for s in resp.sections)
        for mid in misconception_ids:
            m = MISCONCEPTIONS.get(mid)
            if m and any(re.search(rx, text, re.I) for rx in m.signals):
                problems.append(f"misconception_in_answer:{mid}")
        if scheme:
            from .rubrics import point_status
            for p in scheme.points:
                if p.required and point_status(p, text) == "missing":
                    problems.append(f"missing_point:{p.id}")

    # 6. Language.
    if language is not None:
        main = [s.body for s in resp.sections if s.language == language and s.kind != "table"]
        if main and not languages.script_matches(" ".join(main), language):
            problems.append("language_mismatch")
        if language is Language.HINGLISH and any(w in " ".join(main) for w in languages.HEAVY_HINDI):
            problems.append("over_formal_hindi")

    hard = {"source_claim_unsupported", "quote_not_in_source", "citation_unknown_chunk", "citation_locator_mismatch",
            "source_claim_without_citation"}
    if any(p in hard for p in problems):
        resp.grounded = False
    for p in problems:
        resp.audit.append(f"verify:{p}")
    return problems
