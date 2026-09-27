"""Screen, selection, browser, video and document context — the contract, not a reimplementation.

Jarvis already reads the screen (``jarvis.screen_context``, ``jarvis.screen``) and YouTube
captions (``jarvis.screen.youtube``). The companion does not duplicate them: it depends on a
``ContextProvider`` that hands it ``ContextSnapshot``s, and decides what to do with them. After
integration the provider wraps those modules; here, fixtures implement it.

The decisions (``decide``):

* **Is the present context what the question is about?** "This paragraph", "on my screen",
  "what the teacher just said" → yes, and nothing else is consulted. Personal memory is never
  searched for a screen question — an old note must not answer "explain what's on my screen".
* **Is it fresh and readable?** Older than ``STALE_S`` → ask for a new capture. OCR confidence
  below ``documents.USABLE_PAGE`` → ask for a clearer capture rather than guess.
* **Which source is authoritative?** A selection beats the full screen; a PDF page beats a
  screenshot of it; a video's transcript window beats the video title.
* **Past, not present?** "What did the teacher say earlier?" → the transcript history, not the
  screen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .documents import USABLE_PAGE
from .retrieval import terms
from .sources import Page, SourceStore
from .types import AccessClass, ExtractionMethod, SourceType, StudyRequest

STALE_S = 120.0
VIDEO_WINDOW_S = 90.0
_AUTHORITY = {"selection": 5, "pdf_page": 4, "video": 3, "browser": 3, "screen": 2, "image": 2, "overlay": 1}
_KIND_TO_SOURCE = {
    "selection": (SourceType.SCREEN_SELECTION, ExtractionMethod.OCR),
    "screen": (SourceType.SCREEN_SELECTION, ExtractionMethod.OCR),
    "browser": (SourceType.BROWSER_PAGE, ExtractionMethod.DOM),
    "video": (SourceType.VIDEO_TRANSCRIPT, ExtractionMethod.TRANSCRIPT),
    "teacher": (SourceType.TEACHER_TRANSCRIPT, ExtractionMethod.TRANSCRIPT),
    "pdf_page": (SourceType.TEXTBOOK_PDF, ExtractionMethod.TEXT_LAYER),
    "image": (SourceType.PAGE_IMAGE, ExtractionMethod.OCR),
}


@dataclass
class TranscriptLine:
    start_s: float
    text: str


@dataclass
class ContextSnapshot:
    kind: str                        # selection | screen | browser | video | teacher | pdf_page | image | overlay
    title: str = ""                  # window / tab / document title (safe, no URL)
    text: str = ""
    captured_at: float = field(default_factory=time.time)
    ocr_confidence: Optional[float] = None
    page: Optional[int] = None
    position_s: Optional[float] = None               # current playback time, for video
    transcript: list[TranscriptLine] = field(default_factory=list)
    doc_id: str = ""

    def window(self, before_s: float = VIDEO_WINDOW_S, after_s: float = 10.0) -> list[TranscriptLine]:
        if self.position_s is None:
            return self.transcript[-12:]
        return [ln for ln in self.transcript if self.position_s - before_s <= ln.start_s <= self.position_s + after_s]


class ContextProvider(Protocol):
    def snapshots(self) -> list[ContextSnapshot]: ...


class FixtureContextProvider:
    def __init__(self, *snaps: ContextSnapshot) -> None:
        self._snaps = list(snaps)
        self.requests = 0

    def snapshots(self) -> list[ContextSnapshot]:
        self.requests += 1
        return list(self._snaps)


@dataclass
class ContextDecision:
    use: Optional[ContextSnapshot] = None
    present: bool = False            # the question is about what is in front of the student now
    past: bool = False
    need: str = ""                   # "" | "capture" | "clearer_capture" | "selection" | "source"
    reason: str = ""
    allow_memory: bool = True


def decide(req: StudyRequest, snaps: list[ContextSnapshot], now: Optional[float] = None) -> ContextDecision:
    now = now if now is not None else time.time()
    teacher_now = ("teacher" in req.text.lower() and not req.refers_to_past
                   and any(s.kind in ("teacher", "video") for s in snaps))
    present = req.refers_to_screen or teacher_now
    if req.refers_to_past and not present:
        hist = [s for s in snaps if s.kind in ("teacher", "video") and s.transcript]
        return ContextDecision(hist[0] if hist else None, past=True, need="" if hist else "source",
                               reason="past_reference", allow_memory=True)
    if not present:
        # Not asked about the screen: use it only if it is clearly on the same topic.
        q = set(terms(req.text))
        best, score = None, 0.0
        for s in snaps:
            if now - s.captured_at > STALE_S:
                continue
            overlap = len(q & set(terms(s.text))) / (len(q) or 1)
            if overlap > score:
                best, score = s, overlap
        if best is not None and score >= 0.6:
            return ContextDecision(best, present=False, reason="relevant_context")
        return ContextDecision(None, reason="context_not_relevant")

    # The question is about the present screen. Memory is off for this request, whatever happens.
    fresh = [s for s in snaps if now - s.captured_at <= STALE_S]
    if teacher_now:
        fresh = [s for s in fresh if s.kind in ("teacher", "video")] or fresh
    if not fresh:
        return ContextDecision(None, present=True, need="capture", reason="no_fresh_context", allow_memory=False)
    fresh.sort(key=lambda s: -_AUTHORITY.get(s.kind, 0))
    top = fresh[0]
    if top.kind in ("screen", "selection", "image") and top.ocr_confidence is not None and top.ocr_confidence < USABLE_PAGE:
        return ContextDecision(top, present=True, need="clearer_capture", reason="low_ocr_confidence", allow_memory=False)
    if not (top.text.strip() or top.window()):
        return ContextDecision(top, present=True, need="capture", reason="empty_context", allow_memory=False)
    return ContextDecision(top, present=True, reason=f"authoritative:{top.kind}", allow_memory=False)


def ingest_snapshot(store: SourceStore, snap: ContextSnapshot) -> str:
    """Add the snapshot to the source store as a temporary, cited source; returns its doc id."""
    stype, method = _KIND_TO_SOURCE.get(snap.kind, (SourceType.SCREEN_SELECTION, ExtractionMethod.OCR))
    if snap.kind in ("video", "teacher"):
        lines = snap.window()
        pages = [Page(text=ln.text, timestamp_s=ln.start_s) for ln in lines]
    else:
        pages = [Page(text=snap.text, number=snap.page, ocr_confidence=snap.ocr_confidence)]
    doc = store.ingest(pages, title=snap.title or snap.kind, source_type=stype, extraction=method,
                       access=AccessClass.USER_PROVIDED, doc_id=snap.doc_id or "")
    return doc.doc_id
