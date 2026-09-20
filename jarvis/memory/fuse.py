"""Merge two rankings into one, and score keywords properly while we are here.

`recall()` already ran both halves of a hybrid search — embeddings and keywords — and then
printed them as two separate lists under two headings. That is not hybrid retrieval; it is two
searches in a trench coat. The reader has to do the merging, and the merging is the part that
actually improves the answer.

Why both halves are needed at all: dense retrieval fails precisely where a personal assistant
needs precision — a contact's name, a device, an exact command string, a project nobody else
would write down. Embeddings are good at paraphrase and bad at proper nouns; keyword search is
the other way round. Neither is optional.

Why reciprocal rank fusion and not a weighted sum of scores: the two halves produce numbers that
are not comparable. A cosine similarity of 0.82 and a BM25 score of 7.4 cannot be added, and
normalising them means inventing a conversion that changes with every query. RRF only reads the
*position* of a document in each list, which is the one thing both lists agree on the meaning of.

    score(d) = sum over each ranking of 1 / (k + rank(d))

k is a damping constant. At 60 — the value from the original paper and what everyone uses — the
gap between rank 1 and rank 2 is small enough that a document ranked second by both halves beats
one ranked first by only one of them. That is the behaviour worth having: agreement outranks
enthusiasm.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Sequence

RRF_K = 60

# BM25's two knobs, at the values the literature settles on. k1 controls how fast term frequency
# saturates — the fourth mention of a word says much less than the first. b controls how hard
# long documents are penalised; 0.75 is the standard partial normalisation.
BM25_K1 = 1.5
BM25_B = 0.75

_WORD = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def bm25_scores(query: str, documents: Sequence[str]) -> list[float]:
    """Okapi BM25 over a small in-memory corpus.

    The previous keyword search asked one question — does this file contain any of these words —
    and answered it yes or no. So a note that mentions "hackathon" once ranked level with the one
    about nothing else. BM25 is the standard answer to that: rarer words count for more, repeated
    words saturate, and a long document does not win just by being long.
    """
    if not documents:
        return []
    terms = tokenize(query)
    if not terms:
        return [0.0] * len(documents)

    tokenized = [tokenize(d) for d in documents]
    lengths = [len(t) for t in tokenized]
    avg_len = (sum(lengths) / len(lengths)) or 1.0
    counts = [Counter(t) for t in tokenized]
    total = len(documents)

    scores = [0.0] * total
    for term in set(terms):
        containing = sum(1 for c in counts if term in c)
        if containing == 0:
            continue
        # The +0.5/+0.5 smoothing is what keeps a term present in every document from going
        # negative and actively pushing results down.
        idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
        for i, count in enumerate(counts):
            freq = count.get(term, 0)
            if not freq:
                continue
            norm = 1 - BM25_B + BM25_B * (lengths[i] / avg_len)
            scores[i] += idf * (freq * (BM25_K1 + 1)) / (freq + BM25_K1 * norm)
    return scores


def reciprocal_rank_fusion(rankings: Iterable[Sequence[str]], k: int = RRF_K) -> dict[str, float]:
    """One score per key, from its position in each ranking.

    Keys absent from a ranking simply score nothing from it, which is the desired behaviour: a
    document only one half found is not punished for the other half's silence, it just has one
    contribution instead of two.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for position, key in enumerate(ranking):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + position + 1)
    return fused


def fuse(semantic: Sequence[str], keyword: Sequence[str], k: int = RRF_K) -> list[str]:
    """The two halves as one ordered list, best first."""
    scored = reciprocal_rank_fusion([semantic, keyword], k=k)
    # Sorted by score, then by the semantic ordering, so ties are broken the same way twice.
    order = {key: i for i, key in enumerate(list(semantic) + list(keyword))}
    return sorted(scored, key=lambda key: (-scored[key], order.get(key, 1 << 30)))
