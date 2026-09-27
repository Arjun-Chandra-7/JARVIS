"""Source ingestion and provenance: every chunk knows exactly where it came from.

A document enters as pages (or transcript windows) of text, already extracted by ``documents``.
It leaves as ``SourceChunk``s carrying document id, safe title, page, section, source type,
extraction method, language, OCR confidence, ingest time, access class, content hash and char
boundaries. Nothing downstream may drop those fields — citations are built from them, never
from what a model says the page was.

What this module will not do: fetch books from the internet, store the text inside the Git
repository, or hand out long verbatim passages (``excerpt`` is bounded by ``MAX_EXCERPT``).
The store is in memory; ``save``/``load`` persist it under the study data directory only when
the host asks, and ``delete`` removes a document and every chunk of it.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import languages
from .privacy import injection_suspected, safe_title
from .types import AccessClass, ExtractionMethod, SourceChunk, SourceType, content_hash, new_id

MAX_EXCERPT = 240          # characters of verbatim source text a response may quote
CHUNK_CHARS = 700          # target chunk size; paragraphs are kept whole where possible
_HEADING = re.compile(r"^\s*(?P<num>\d{1,2}(?:\.\d{1,2}){1,2})\s+(?P<title>[A-Z][^\n]{2,80})$", re.M)


@dataclass
class Page:
    """One page (or transcript window) of extracted text."""
    text: str
    number: Optional[int] = None
    timestamp_s: Optional[float] = None
    ocr_confidence: Optional[float] = None
    uncertain_words: tuple[str, ...] = ()


@dataclass
class Document:
    doc_id: str
    title: str
    source_type: SourceType
    extraction: ExtractionMethod
    access: AccessClass
    chapter: str = ""
    language: str = "en"
    ingested_at: float = field(default_factory=time.time)
    pages: int = 0
    chunk_ids: list[str] = field(default_factory=list)
    suspicious: bool = False


class SourceStore:
    def __init__(self) -> None:
        self.docs: dict[str, Document] = {}
        self.chunks: dict[str, SourceChunk] = {}

    # ------------------------------------------------------------------------ ingest
    def ingest(self, pages: Iterable[Page], *, title: str, source_type: SourceType,
               extraction: ExtractionMethod, access: AccessClass = AccessClass.USER_PROVIDED,
               chapter: str = "", doc_id: str = "") -> Document:
        pages = list(pages)
        doc = Document(doc_id=doc_id or new_id("D"), title=safe_title(title), source_type=source_type,
                       extraction=extraction, access=access, chapter=chapter, pages=len(pages))
        section = ""
        for page in pages:
            for start, end, text, sec in _split(page.text, section):
                section = sec or section
                lang = str(languages.detect(text))
                words = tuple(w for w in page.uncertain_words if w in text)
                chunk = SourceChunk(
                    chunk_id=f"{doc.doc_id}:{page.number or 0}:{start}", doc_id=doc.doc_id, title=doc.title,
                    text=text, source_type=source_type, extraction=extraction, access=access,
                    page=page.number, section=section, chapter=chapter, timestamp_s=page.timestamp_s,
                    language=lang, ocr_confidence=page.ocr_confidence, uncertain_words=words,
                    start=start, end=end, ingested_at=doc.ingested_at, hash=content_hash(text),
                    suspicious=injection_suspected(text))
                self.chunks[chunk.chunk_id] = chunk
                doc.chunk_ids.append(chunk.chunk_id)
                doc.suspicious = doc.suspicious or chunk.suspicious
        langs = [self.chunks[c].language for c in doc.chunk_ids]
        doc.language = max(set(langs), key=langs.count) if langs else "en"
        self.docs[doc.doc_id] = doc
        return doc

    def ingest_text(self, text: str, **kw) -> Document:
        kw.setdefault("extraction", ExtractionMethod.TYPED)
        return self.ingest([Page(text=text)], **kw)

    # ------------------------------------------------------------------------ access
    def doc_chunks(self, doc_ids: Optional[Iterable[str]] = None) -> list[SourceChunk]:
        ids = set(doc_ids) if doc_ids is not None else set(self.docs)
        return [c for c in self.chunks.values() if c.doc_id in ids]

    def delete(self, doc_id: str) -> bool:
        doc = self.docs.pop(doc_id, None)
        if not doc:
            return False
        for cid in doc.chunk_ids:
            self.chunks.pop(cid, None)
        return True

    def clear(self) -> None:
        self.docs.clear()
        self.chunks.clear()

    # ------------------------------------------------------------------------ persistence
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"docs": [asdict(d) for d in self.docs.values()],
                "chunks": [asdict(c) for c in self.chunks.values()]}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        path.chmod(0o600)

    @classmethod
    def load(cls, path: Path) -> "SourceStore":
        s = cls()
        if not path.exists():
            return s
        data = json.loads(path.read_text(encoding="utf-8"))
        for d in data.get("docs", []):
            d["source_type"], d["extraction"], d["access"] = (
                SourceType(d["source_type"]), ExtractionMethod(d["extraction"]), AccessClass(d["access"]))
            s.docs[d["doc_id"]] = Document(**d)
        for c in data.get("chunks", []):
            c["source_type"], c["extraction"], c["access"] = (
                SourceType(c["source_type"]), ExtractionMethod(c["extraction"]), AccessClass(c["access"]))
            c["uncertain_words"] = tuple(c.get("uncertain_words", ()))
            s.chunks[c["chunk_id"]] = SourceChunk(**c)
        return s


def excerpt(text: str, around: str = "", limit: int = MAX_EXCERPT) -> str:
    """A short quotation: the sentence containing ``around`` if given, cut to ``limit``."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if around:
        for sent in re.split(r"(?<=[.!?।])\s+", text):
            if around.lower() in sent.lower():
                text = sent
                break
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _split(text: str, section: str) -> list[tuple[int, int, str, str]]:
    """Paragraph-preserving chunks with their char offsets and the section heading in force."""
    out: list[tuple[int, int, str, str]] = []
    paras = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text, re.S)]
    buf_start, buf_end, buf, cur = None, 0, [], section
    for start, end, para in paras:
        h = _HEADING.match(para.split("\n", 1)[0])
        new_sec = h.group("num") + " " + h.group("title").strip() if h else ""
        if buf and (new_sec or sum(len(p) for p in buf) + len(para) > CHUNK_CHARS):
            out.append((buf_start, buf_end, "\n\n".join(buf), cur))
            buf, buf_start = [], None
        if new_sec:
            cur = new_sec
        if buf_start is None:
            buf_start = start
        buf.append(para.strip())
        buf_end = end
    if buf:
        out.append((buf_start or 0, buf_end, "\n\n".join(buf), cur))
    return out
