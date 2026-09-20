"""Search your own documents, and say where in them the answer was.

Ranked with the same BM25 the vault's keyword half uses, over text extracted by `documents`.
Deliberately no embeddings: a document search is almost always aimed at a proper noun — a project
name, a person, an invoice number — and that is the case dense retrieval is worst at. Adding an
embedding pass here would cost an index, a model and a rebuild, to be worse at the thing being
asked.

No index on disk, either. Extracting a few hundred documents takes under a second with poppler
doing the PDFs, which is faster than deciding whether a cached index is stale. An index is a
thing that can be wrong; a fresh read cannot be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import documents, fuse

# Enough of the document to see why it matched, not so much that five results fill the context.
SNIPPET_CHARS = 240


@dataclass
class Hit:
    path: Path
    score: float
    snippet: str
    kind: str

    @property
    def name(self) -> str:
        return self.path.name


def _best_snippet(text: str, query: str) -> str:
    """The stretch of the document where the most of the query appears.

    Showing the first paragraph of a match is nearly useless — it is the title page of a PDF. The
    window that contains the query words is the one that says why this file came back.
    """
    terms = [t for t in fuse.tokenize(query) if len(t) > 2]
    if not terms:
        return " ".join(text.split())[:SNIPPET_CHARS]

    lowered = text.lower()
    best_at, best_hits = 0, -1
    for match in re.finditer("|".join(re.escape(t) for t in terms), lowered):
        start = max(0, match.start() - SNIPPET_CHARS // 3)
        window = lowered[start:start + SNIPPET_CHARS]
        hits = sum(window.count(t) for t in terms)
        if hits > best_hits:
            best_at, best_hits = start, hits
    snippet = " ".join(text[best_at:best_at + SNIPPET_CHARS].split())
    return ("…" if best_at else "") + snippet


def search(query: str, limit: int = 5, where: Optional[list[Path]] = None) -> list[Hit]:
    """The documents that best answer `query`, best first."""
    asked = (query or "").strip()
    if not asked:
        return []

    found: list[documents.Document] = []
    for path in documents.walk(where):
        doc = documents.extract(path)
        if doc is not None:
            found.append(doc)
    if not found:
        return []

    scores = fuse.bm25_scores(asked, [d.text for d in found])
    ranked = sorted(zip(found, scores), key=lambda pair: -pair[1])
    return [
        Hit(path=doc.path, score=score, snippet=_best_snippet(doc.text, asked), kind=doc.kind)
        for doc, score in ranked[:limit]
        if score > 0                      # a zero score matched nothing; do not pad the list
    ]


def readable(query: str, hits: list[Hit]) -> str:
    """What gets read back, as prose rather than a table."""
    if not hits:
        return (f"Nothing in your documents matches '{query}'. "
                f"I looked in {', '.join(str(r) for r in documents.roots())}.")
    lines = []
    for hit in hits:
        lines.append(f"{hit.name}  ({hit.kind}, {hit.path.parent})\n  {hit.snippet}")
    return "\n\n".join(lines)
