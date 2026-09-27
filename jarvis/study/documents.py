"""PDF pages, page images and handwriting → ``sources.Page``s, with confidence kept.

No OCR engine lives here. Jarvis already reads the screen (``jarvis.screen_context``); a photo of
a textbook page or a handwritten answer goes through whatever implements ``OcrEngine`` — after
integration, the existing screen OCR — and this module only turns its words-with-confidence into
pages that remember which words were uncertain.

PDFs use their own text layer through ``pypdf`` (already a dependency for document search).
A page with no text layer is reported as needing OCR rather than silently skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from .sources import Page

LOW_CONFIDENCE = 0.60        # a word below this is "uncertain": shown to the student, never penalised
USABLE_PAGE = 0.55           # a page whose mean confidence is below this needs a better capture


@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: float
    line: int = 0


@dataclass
class OcrResult:
    words: list[OcrWord] = field(default_factory=list)
    handwriting: bool = False

    @property
    def mean_confidence(self) -> float:
        return sum(w.confidence for w in self.words) / len(self.words) if self.words else 0.0

    @property
    def uncertain(self) -> list[OcrWord]:
        return [w for w in self.words if w.confidence < LOW_CONFIDENCE]

    def lines(self) -> list[str]:
        out: dict[int, list[str]] = {}
        for w in self.words:
            out.setdefault(w.line, []).append(w.text)
        return [" ".join(ws) for _, ws in sorted(out.items())]

    def text(self) -> str:
        return "\n".join(self.lines())

    def marked_text(self) -> str:
        """The text with uncertain words bracketed: "the [iens?] move" — for the student to see."""
        out: dict[int, list[str]] = {}
        for w in self.words:
            out.setdefault(w.line, []).append(f"[{w.text}?]" if w.confidence < LOW_CONFIDENCE else w.text)
        return "\n".join(" ".join(ws) for _, ws in sorted(out.items()))


class OcrEngine(Protocol):
    def read(self, image: bytes, *, handwriting: bool = False) -> OcrResult: ...


def page_from_ocr(result: OcrResult, number: Optional[int] = None) -> Page:
    return Page(text=result.text(), number=number, ocr_confidence=round(result.mean_confidence, 3),
                uncertain_words=tuple(dict.fromkeys(w.text for w in result.uncertain)))


@dataclass
class CaptureQuality:
    usable: bool
    reason: str = ""
    uncertain: list[str] = field(default_factory=list)


def assess(result: OcrResult) -> CaptureQuality:
    """Is this capture good enough to read? If not, ask for a better one — don't guess."""
    if not result.words:
        return CaptureQuality(False, "no_text_found")
    if result.mean_confidence < USABLE_PAGE:
        return CaptureQuality(False, "low_confidence", [w.text for w in result.uncertain])
    return CaptureQuality(True, "", [w.text for w in result.uncertain])


# ------------------------------------------------------------------------------------ PDFs
@dataclass
class PdfExtraction:
    pages: list[Page]
    needs_ocr: list[int]             # 1-based page numbers with no text layer


def pdf_pages(path: Path, max_pages: int = 400) -> PdfExtraction:
    """Text of each page from the PDF's own text layer. Page numbers are 1-based PDF pages."""
    from pypdf import PdfReader      # already a Jarvis dependency; imported lazily

    reader = PdfReader(str(path))
    pages, needs = [], []
    for i, p in enumerate(reader.pages[:max_pages], start=1):
        text = _tidy(p.extract_text() or "")
        if len(text.strip()) < 20:
            needs.append(i)
            continue
        pages.append(Page(text=text, number=i))
    return PdfExtraction(pages, needs)


def _tidy(text: str) -> str:
    text = re.sub(r"-\n(?=[a-z])", "", text)            # re-join hyphenated line breaks
    text = re.sub(r"(?<![.\n:])\n(?!\n|\s*\d+(?:\.\d+)+\s)", " ", text)   # unwrap soft line breaks
    return re.sub(r"[ \t]{2,}", " ", text).strip()
