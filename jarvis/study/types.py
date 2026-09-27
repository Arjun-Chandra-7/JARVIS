"""Shared typed models for the Study Companion.

Plain dataclasses and string enums, the way the rest of Jarvis writes them: they serialise with
``asdict`` and compare by value, and nothing here does I/O.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class _Str(str, Enum):
    def __str__(self) -> str:  # "explain", not "TaskType.EXPLAIN", in logs and JSON
        return self.value


class TaskType(_Str):
    EXPLAIN = "explain"
    DEFINE = "define"
    COMPARE = "compare"
    DERIVE = "derive"
    SOLVE = "solve"
    PROVE = "prove"
    ANSWER = "answer"            # a written (exam) answer to a question
    SUMMARIZE = "summarize"
    REVISE = "revise"
    QUIZ = "quiz"
    EVALUATE = "evaluate"        # "check my answer"
    CORRECT = "correct"          # "fix my answer"
    HINT = "hint"
    DIAGRAM = "diagram"
    FLASHCARDS = "flashcards"
    PLAN = "plan"                # a revision / study plan
    QUOTE = "quote"              # exact source wording
    SESSION = "session"          # start / pause / end a study session


class AnswerMode(_Str):
    UNDERSTAND = "understand"
    EXAM = "exam"
    REVISION = "revision"        # last-minute
    STEPWISE = "stepwise"
    HINT = "hint"
    LINE_BY_LINE = "line_by_line"
    COMPARE = "compare"
    SHORT = "short"              # "only the final answer" / "in one line"


class Language(_Str):
    ENGLISH = "en"
    HINDI = "hi"                 # Devanagari Hindi
    HINGLISH = "hinglish"        # Hindi in Latin letters, mixed with English


class InputSource(_Str):
    TYPED = "typed"
    VOICE = "voice"
    PASTED = "pasted"
    SCREEN = "screen"
    CAPTURE = "capture"          # a photo / scan of written work
    QUIZ = "quiz"


class SourceType(_Str):
    TEXTBOOK_PDF = "textbook_pdf"
    PAGE_IMAGE = "page_image"
    NOTES = "notes"
    QUESTION_PAPER = "question_paper"
    MARKING_SCHEME = "marking_scheme"
    BROWSER_PAGE = "browser_page"
    SCREEN_SELECTION = "screen_selection"
    VIDEO_TRANSCRIPT = "video_transcript"
    TEACHER_TRANSCRIPT = "teacher_transcript"
    TYPED_NOTES = "typed_notes"
    LOCAL_FOLDER = "local_folder"


class ExtractionMethod(_Str):
    TEXT_LAYER = "text_layer"    # a PDF's own text
    OCR = "ocr"
    TRANSCRIPT = "transcript"
    TYPED = "typed"
    DOM = "dom"                  # a web page's text


class AccessClass(_Str):
    """Where a source came from and so what may be done with it."""
    USER_OWNED = "user_owned"            # the student's own notes / answers
    USER_PROVIDED = "user_provided"      # material the student supplied (may be copyrighted)
    OFFICIAL_OPEN = "official_open"      # officially published and freely available
    SYNTHETIC = "synthetic"              # fixtures


class Coverage(_Str):
    """How much trust a curriculum entry deserves. Shown to the student, never upgraded silently."""
    OFFICIAL_SOURCE = "official_source"      # backed by an official document the student supplied
    GENERAL = "general"                      # general Class 10 knowledge, "NCERT-style"
    USER_IMPORTED = "user_imported"          # built from the student's own imported chapter
    UNVERIFIED = "unverified"                # named but not checked; may not be in the syllabus


class Privacy(_Str):
    PUBLIC = "public"                # the question alone, no personal material
    PERSONAL = "personal"            # the student's answers, notes, history
    SENSITIVE = "sensitive"          # handwriting images, voice, school documents


class Detail(_Str):
    BRIEF = "brief"
    NORMAL = "normal"
    DETAILED = "detailed"


class OutputFormat(_Str):
    PROSE = "prose"
    POINTS = "points"
    TABLE = "table"
    STEPS = "steps"
    SPOKEN = "spoken"
    FLASHCARDS = "flashcards"
    JSON = "json"


class SupportKind(_Str):
    """What a sentence in an answer rests on."""
    SOURCE = "source"                # directly supported by a retrieved chunk
    GENERAL = "general"              # general explanatory knowledge, not from the supplied source
    INFERENCE = "inference"          # reasoned from the source, not stated in it
    COMPUTED = "computed"            # produced and checked by the math/science engine


def new_id(prefix: str = "S") -> str:
    return prefix + uuid.uuid4().hex[:12]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------- sources
@dataclass(frozen=True)
class SourceChunk:
    """One retrievable piece of a document, with everything needed to cite it honestly."""
    chunk_id: str
    doc_id: str
    title: str                       # a safe title: never an absolute path
    text: str
    source_type: SourceType
    extraction: ExtractionMethod
    access: AccessClass
    page: Optional[int] = None       # 1-based printed page, when the document has pages
    section: str = ""
    chapter: str = ""
    timestamp_s: Optional[float] = None   # for transcripts
    language: str = "en"
    ocr_confidence: Optional[float] = None
    uncertain_words: tuple[str, ...] = ()
    start: int = 0                   # char offsets within the page / transcript window
    end: int = 0
    ingested_at: float = 0.0
    hash: str = ""
    suspicious: bool = False         # carries text that tries to instruct the assistant

    def locator(self) -> str:
        """"p. 3, §12.2" or "at 4:05" — only what is actually known."""
        bits = []
        if self.page is not None:
            bits.append(f"p. {self.page}")
        if self.section:
            bits.append(f"§{self.section}")
        if self.timestamp_s is not None:
            m, s = divmod(int(self.timestamp_s), 60)
            bits.append(f"at {m}:{s:02d}")
        return ", ".join(bits)


@dataclass(frozen=True)
class Citation:
    chunk_id: str
    doc_id: str
    title: str
    locator: str
    excerpt: str = ""                # a short excerpt only (see sources.MAX_EXCERPT)


@dataclass
class Claim:
    text: str
    support: SupportKind
    citations: list[Citation] = field(default_factory=list)


# ---------------------------------------------------------------------------------- request
@dataclass
class ContextRef:
    """A pointer to the screen / video / page context that came with the request."""
    kind: str                        # "screen" | "selection" | "browser" | "video" | "pdf_page" | "image"
    title: str = ""
    doc_id: str = ""
    page: Optional[int] = None
    timestamp_s: Optional[float] = None
    captured_at: float = 0.0
    ocr_confidence: Optional[float] = None


@dataclass
class StudyRequest:
    text: str
    request_id: str = field(default_factory=lambda: new_id("R"))
    input_source: InputSource = InputSource.TYPED
    language: Language = Language.ENGLISH
    response_language: Language = Language.ENGLISH
    exam_language: Optional[Language] = None     # "exam answer in English, explain in Hinglish"
    grade: int = 10
    board: str = "CBSE"
    subject: str = ""
    chapter: str = ""
    topic: str = ""
    task: TaskType = TaskType.EXPLAIN
    mode: AnswerMode = AnswerMode.UNDERSTAND
    marks: Optional[int] = None
    detail: Detail = Detail.NORMAL
    exam_mode: bool = False
    source_required: bool = False                # "according to this page", "use only this chapter"
    exact_wording: bool = False                  # "exact textbook answer", "what is written"
    references: list[str] = field(default_factory=list)   # doc ids the student pointed at
    context: Optional[ContextRef] = None
    refers_to_screen: bool = False
    refers_to_past: bool = False                 # "what did the teacher say earlier"
    previous_attempts: list[str] = field(default_factory=list)
    known_mastery: dict[str, float] = field(default_factory=dict)
    output_format: OutputFormat = OutputFormat.PROSE
    diagram: bool = False
    compute: bool = False                        # needs the calculator / symbolic engine
    privacy: Privacy = Privacy.PUBLIC
    confidence: float = 0.0
    missing: list[str] = field(default_factory=list)
    student_answer: str = ""                     # for evaluate / correct
    question: str = ""                           # the question part, when separable
    hint_level: int = 0
    signals: list[str] = field(default_factory=list)   # which rules fired, for tests and debugging

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------------- answers
@dataclass
class Section:
    """One labelled part of a response: "Samjho", "Exam answer (3 marks)", "Check yourself"."""
    heading: str
    body: str
    language: Language = Language.ENGLISH
    kind: str = "text"               # "text" | "points" | "table" | "steps" | "check" | "note"


@dataclass
class StudyResponse:
    request_id: str
    task: TaskType
    mode: AnswerMode
    language: Language
    sections: list[Section] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    label: str = ""                  # "NCERT-style", "From your PDF", "Class 10-level"
    grounded: bool = False           # every source-specific claim is backed by a chunk
    source_missing: bool = False     # the supplied source does not contain the answer
    uncertainty: list[str] = field(default_factory=list)
    follow_up: str = ""              # the comprehension check / next question
    visual: Optional[Any] = None     # a visuals.TeachingVisualRequest
    verification: list[str] = field(default_factory=list)   # what was checked, deterministically
    needs: list[str] = field(default_factory=list)          # missing information to ask for
    audit: list[str] = field(default_factory=list)          # safe audit events (no content)
    used_memory: bool = False        # personal memory consulted (never for screen questions)
    brain_calls: int = 0

    def text(self) -> str:
        parts = []
        if self.label:
            parts.append(f"[{self.label}]")
        for s in self.sections:
            parts.append(f"{s.heading}\n{s.body}" if s.heading else s.body)
        if self.citations:
            parts.append("Sources: " + "; ".join(
                f"{c.title}{', ' + c.locator if c.locator else ''}" for c in self.citations))
        if self.follow_up:
            parts.append(self.follow_up)
        return "\n\n".join(p for p in parts if p)

    def section(self, heading_prefix: str) -> Optional[Section]:
        for s in self.sections:
            if s.heading.lower().startswith(heading_prefix.lower()):
                return s
        return None


# ---------------------------------------------------------------------------------- marking
@dataclass
class ScoringPoint:
    """One idea an examiner looks for. ``keywords`` are alternatives: any group fully present
    counts. ``marks`` may be fractional (half marks are real in CBSE step marking)."""
    id: str
    idea: str
    keywords: list[list[str]]
    marks: float = 1.0
    required: bool = True
    kind: str = "idea"               # "idea" | "formula" | "diagram" | "term" | "step" | "unit"


@dataclass
class MarkScheme:
    total: int
    points: list[ScoringPoint]
    optional: list[ScoringPoint] = field(default_factory=list)
    formulae: list[str] = field(default_factory=list)
    diagram_required: bool = False
    terminology: list[str] = field(default_factory=list)
    step_marks: bool = False
    deductions: list[str] = field(default_factory=list)
    max_words: int = 0
    official: bool = False           # from a real marking scheme the student supplied
    source: str = ""                 # citation of that scheme, when official


@dataclass
class Finding:
    dimension: str                   # factual|completeness|reasoning|calculation|terminology|presentation|language
    verdict: str                     # "correct" | "missing" | "inaccurate" | "uncertain"
    detail: str
    line: Optional[int] = None       # 1-based line of the student's answer


@dataclass
class Evaluation:
    correct: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    inaccurate: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    wrong_line: Optional[int] = None
    marks_estimate: str = ""         # "likely 2/3", "approximately 3–4 marks", or the can't-grade note
    graded: bool = False
    corrected: str = ""
    next_step: str = ""
    confidence: float = 0.0
    uncertain_words: list[str] = field(default_factory=list)
    clarify: str = ""                # a question to ask, only when the uncertainty matters
