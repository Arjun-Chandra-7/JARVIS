"""Source-grounded retrieval over the student's own material.

Lexical BM25 with light stemming and the glossary's variants, scoped to the documents a request
may use. It is deliberately simple and local: chapters are small, and a lexical match is
explainable — the citation shows the words that matched. An embedding retriever can replace
``Retriever.score`` after integration without changing callers.

The important part is the floor. ``search`` returns nothing rather than the "least bad" chunk
when no chunk shares enough of the question's content words, and ``supports`` answers "does this
chunk actually contain this claim?" — which is how "the source does not say" gets detected
instead of papered over.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .types import SourceChunk

_WORD = re.compile(r"[a-z0-9]+|[ऀ-ॿ]+")
STOP = frozenset("""a an the of to in on at for from by with and or but is are was were be been being it its this
that these those as into than then so such which who whom whose what when where why how do does did can could
will would should may might must shall there their they them he she his her we our you your i me my not no yes
also about according page chapter says say said tell explain give answer only use text book textbook ncert
written paragraph section per above below please""".split())
# Hinglish function words are not content either.
STOP |= frozenset("kya kyun kyu hai hota hoti ka ki ke ko se mein me aur ya nahi bhi batao samjhao".split())
# Question scaffolding: words that shape a question without naming its content.
STOP |= frozenset("""happen happens happened between mean means meant called name named following occur occurs
describe state write list main important show find whom used""".split())


def stem(w: str) -> str:
    """Light, consistent stemming: "releases", "released", "release" → "releas"."""
    if w.isdigit():
        return w
    for suf in ("ies", "ing", "ed", "es", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            if suf == "es" and not w.endswith(("ses", "xes", "zes", "ches", "shes")):
                suf = "s"
            w = w[: -len(suf)] + ("y" if suf == "ies" else "")
            break
    return w[:-1] if len(w) > 4 and w.endswith("e") else w


def terms(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall((text or "").lower()) if w not in STOP and len(w) > 1]


@dataclass(frozen=True)
class Hit:
    chunk: SourceChunk
    score: float
    matched: tuple[str, ...]
    coverage: float                  # share of the query's content terms found in the chunk


class Retriever:
    def __init__(self, chunks: Iterable[SourceChunk]) -> None:
        self.chunks = list(chunks)
        self.tf = [self._counts(c.text) for c in self.chunks]
        n = len(self.chunks) or 1
        df: dict[str, int] = {}
        for tf in self.tf:
            for t in tf:
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.avg = sum(sum(tf.values()) for tf in self.tf) / n or 1.0

    @staticmethod
    def _counts(text: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in terms(text):
            out[t] = out.get(t, 0) + 1
        return out

    def search(self, query: str, k: int = 3, min_coverage: float = 0.6,
               page: Optional[int] = None) -> list[Hit]:
        q = list(dict.fromkeys(terms(query)))
        if not q:
            return []
        hits = []
        for c, tf in zip(self.chunks, self.tf):
            if page is not None and c.page != page:
                continue
            matched = tuple(t for t in q if t in tf)
            if not matched:
                continue
            cov = len(matched) / len(q)
            if cov < min_coverage:
                continue
            length = sum(tf.values()) or 1
            score = sum(self.idf.get(t, 0) * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * length / self.avg))
                        for t in matched)
            hits.append(Hit(c, round(score * (0.5 + cov), 4), matched, round(cov, 3)))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]


def supports(chunk: SourceChunk, claim: str, threshold: float = 0.6) -> bool:
    """Does the chunk contain the content words of ``claim``? Numbers must match exactly."""
    ct = set(terms(chunk.text))
    cl = [t for t in terms(claim)]
    if not cl:
        return False
    nums = [t for t in cl if t.isdigit()]
    if any(n not in ct for n in nums):
        return False
    return sum(1 for t in cl if t in ct) / len(cl) >= threshold


def best_sentence(chunk: SourceChunk, query: str) -> tuple[str, float]:
    """The chunk sentence that shares most of the query's content terms, and that share."""
    q = set(terms(query))
    best, cov = "", 0.0
    for sent in re.split(r"(?<=[.!?\u0964])\s+|\n{2,}", chunk.text):
        st = set(terms(sent))
        c = len(q & st) / (len(q) or 1)
        if c > cov:
            best, cov = sent.strip(), c
    return best, round(cov, 3)
