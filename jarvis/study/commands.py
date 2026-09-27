"""The Study Companion: voice and text study requests in, verified study responses out.

``StudyCompanion.ask(text)`` is the whole pipeline:

1. study/session/privacy commands ("pause studying", "delete my study history") are handled
   directly;
2. an answer to a waiting quiz question is graded;
3. otherwise ``intent.extract`` builds a typed ``StudyRequest`` (no model call);
4. for a question about the screen / a video / what the teacher just said, ``context.decide``
   chooses the authoritative snapshot, or asks for a capture — and personal memory is off;
5. the request goes to the path that can answer it best, in this order of trust: the student's
   own source (grounded, cited) → the deterministic engines → a verified offline card → the brain
   through the gateway (labelled as such) → an honest "I can't answer that reliably";
6. ``verify.check`` runs on the result; secrets are scrubbed from anything a model wrote;
7. mastery, session and private-safe metrics are updated.

Integration: the Daily Brain router calls ``looks_like_study`` to decide whether to hand a
turn here, and constructs the companion with its capability-router adapter as the gateway
(docs/STUDY_COMPANION.md).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

from . import answer_modes as modes
from . import context as ctx
from . import exports, intent, math_engine, rubrics, science_engine, verify, visuals
from .curriculum import MISCONCEPTIONS, REGISTRY, Registry
from .documents import OcrResult, assess, page_from_ocr, pdf_pages
from .gateway import BrainRequest, BrainTask, GuardedGateway, StudyBrainGateway, UnavailableGateway
from .knowledge import CARDS, Card, card_for
from .mastery import MasteryStore
from .privacy import AuditLog, PrivacyPolicy, scrub_for_prompt
from .quiz import BANK, QuizEngine
from .retrieval import Retriever, best_sentence
from .revision import plan as revision_plan
from .session import SessionManager
from .sources import MAX_EXCERPT, Page, SourceStore, excerpt
from .telemetry import Metrics
from .types import (AccessClass, AnswerMode, Citation, Claim, Coverage, ExtractionMethod, InputSource, Language,
                    Privacy, Section, SourceChunk, SourceType, StudyRequest, StudyResponse, SupportKind, TaskType)

EN, HG, HI = Language.ENGLISH, Language.HINGLISH, Language.HINDI


def default_data_dir() -> Path:
    return Path(os.environ.get("JARVIS_STUDY_DIR", "~/.config/jarvis/study")).expanduser()


_STUDY_HINT = re.compile(
    r"(?i)\b(?:explain|samjha|quiz\s+me|marks?\b|exam\s+answer|check\s+my\s+(?:answer|solution)|hint|revision|revise|"
    r"study|chapter|ncert|cbse|class\s*10|formula|derive|prove|solve|flashcards?|homework|syllabus|teacher|"
    r"cumulative\s+frequency|electricity|refraction|photosynthesis)\b|अंक|समझा|पाठ|भावार्थ")


def looks_like_study(text: str) -> bool:
    """Cheap pre-check for the Daily Brain router: is this turn worth offering to the companion?"""
    return bool(_STUDY_HINT.search(text or ""))


_SESSION = [
    ("start", re.compile(r"(?i)\b(?:start|begin)\b.{0,25}\b(?:study|studying|session|padhai)\b")),
    ("studying", re.compile(r"(?i)\bwe(?:'re| are)\s+studying\s+(?P<what>.+)$")),
    ("explain_then_quiz", re.compile(r"(?i)\bexplain\s+first,?\s+then\s+quiz\b")),
    ("hints_only", re.compile(r"(?i)\bonly\s+(?:give\s+)?hints\b|\bhints\s+only\b")),
    ("pause", re.compile(r"(?i)\bpause\s+(?:studying|the\s+session|study)\b|\bstudy\s+break\b")),
    ("resume", re.compile(r"(?i)\bcontinue\s+from\s+where\b|\bresume\s+(?:studying|the\s+session|study)\b")),
    ("wrong", re.compile(r"(?i)\bwhat\s+did\s+i\s+get\s+wrong\b|\bmy\s+mistakes\b")),
    ("end", re.compile(r"(?i)\bend\s+(?:the\s+)?(?:study\s+)?session\b|\bstop\s+studying\b")),
    ("summary", re.compile(r"(?i)\b(?:give\s+me\s+a\s+)?(?:session\s+)?summary\s+of\s+(?:the\s+)?session\b|^give\s+me\s+a\s+summary\.?$")),
    ("progress", re.compile(r"(?i)\b(?:show|view)\s+my\s+(?:study\s+)?progress\b|\bhow\s+am\s+i\s+doing\b")),
    ("reset", re.compile(r"(?i)\breset\s+(?:my\s+progress\s+(?:in|on|for)\s+)?(?P<what>.+?)(?:\s+progress)?$")),
    ("delete", re.compile(r"(?i)\bdelete\s+(?:all\s+)?my\s+study\s+(?:history|data)\b")),
    ("personal_off", re.compile(r"(?i)\b(?:turn\s+off|disable|stop)\s+personali[sz]ation\b")),
    ("personal_on", re.compile(r"(?i)\b(?:turn\s+on|enable)\s+personali[sz]ation\b")),
]
_JUST_SAID = re.compile(r"(?i)\b(?:just|last)\b.{0,20}\b(?:say|said|told|explain(?:ed)?|bol[ae])\b|"
                        r"\bwhat\s+(?:did|was)\s+(?:the\s+)?(?:teacher|sir|ma'?am|he|she)\s+(?:say|saying)\b")
_DEICTIC = re.compile(r"(?i)\b(?:iska|iski|iske|isko|this|that|it)\b|इसका|इसकी|इसके|इसे")
_NEXT_Q = re.compile(r"(?i)^\s*(?:next(?:\s+question)?|another\s+one|agla(?:\s+sawaal)?|continue)\s*[.!]?\s*$")
_STOP_QUIZ = re.compile(r"(?i)\b(?:stop|end|quit)\s+(?:the\s+)?quiz\b")
_REVEAL = re.compile(r"(?i)\b(?:show|tell|reveal)\s+(?:me\s+)?the\s+(?:full\s+)?(?:answer|solution)\b")
_MINUTES = re.compile(r"(?i)\b(\d{1,3})\s*(min|minutes|mins|hours?|hrs?)\b")
_CHAPTER_RANGE = re.compile(r"(?i)\bchapters?\s+(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\b")
_Q_WORDS = {"resistance": "R", "current": "I", "potential difference": "V", "voltage": "V", "heat": "H",
            "power": "P", "charge": "Q", "time": "t", "work": "W", "focal length": "f", "refractive index": "n",
            "equivalent resistance": "Rp", "resistivity": "rho"}
_UNIT_SYM = {"V": "V", "A": "I", "ohm": "R", "C": "Q", "s": "t", "J": "W", "W": "P", "D": "P", "m": "l"}


class StudyCompanion:
    def __init__(self, gateway: Optional[StudyBrainGateway] = None, *, data_dir: Optional[Path] = None,
                 policy: Optional[PrivacyPolicy] = None, registry: Registry = REGISTRY,
                 context_provider: Optional[ctx.ContextProvider] = None,
                 renderer: Optional[visuals.FakeRenderer] = None,
                 recall: Optional[Callable[[str], list[str]]] = None) -> None:
        self.policy = policy or PrivacyPolicy()
        self.gateway = GuardedGateway(gateway or UnavailableGateway(), self.policy)
        self.registry = registry
        self.store = SourceStore()
        self.mastery = MasteryStore(data_dir / "mastery.json" if data_dir else None,
                                    personalization=self.policy.personalization)
        self.sessions = SessionManager(data_dir / "session.json" if data_dir else None)
        self.audit = AuditLog()
        self.metrics = Metrics()
        self.context_provider = context_provider
        self.renderer = renderer
        self.recall = recall                 # personal memory lookup (integration); never used for screen questions
        self.recall_calls = 0
        self.quiz: Optional[QuizEngine] = None
        self.last_plan = None                # the latest revision.RevisionPlan, never acted on here
        self._hints: dict[str, tuple[modes.HintLadder, int]] = {}
        self._last_hint_key = ""

    # ================================================================== sources
    def add_text(self, text: str, *, title: str, source_type: SourceType = SourceType.NOTES,
                 access: AccessClass = AccessClass.USER_PROVIDED, chapter: str = "") -> str:
        doc = self.store.ingest_text(text, title=title, source_type=source_type, access=access, chapter=chapter)
        return self._audit_doc(doc.doc_id)

    def add_pages(self, pages: list[Page], *, title: str, source_type: SourceType = SourceType.TEXTBOOK_PDF,
                  extraction: ExtractionMethod = ExtractionMethod.TEXT_LAYER, chapter: str = "",
                  access: AccessClass = AccessClass.USER_PROVIDED) -> str:
        doc = self.store.ingest(pages, title=title, source_type=source_type, extraction=extraction,
                                access=access, chapter=chapter)
        return self._audit_doc(doc.doc_id)

    def add_pdf(self, path: Path, *, chapter: str = "", source_type: SourceType = SourceType.TEXTBOOK_PDF) -> str:
        ext = pdf_pages(path)
        doc_id = self.add_pages(ext.pages, title=path.name, source_type=source_type, chapter=chapter)
        if ext.needs_ocr:
            self.audit.record(event="pdf_pages_need_ocr", doc_id=doc_id, count=len(ext.needs_ocr))
        return doc_id

    def add_ocr(self, result: OcrResult, *, title: str, page: Optional[int] = None,
                source_type: SourceType = SourceType.PAGE_IMAGE) -> str:
        doc = self.store.ingest([page_from_ocr(result, page)], title=title, source_type=source_type,
                                extraction=ExtractionMethod.OCR)
        return self._audit_doc(doc.doc_id)

    def _audit_doc(self, doc_id: str) -> str:
        doc = self.store.docs[doc_id]
        self.audit.record(event="source_ingested", doc_id=doc_id, pages=doc.pages, chunks=len(doc.chunk_ids),
                          suspicious=doc.suspicious)
        if doc.suspicious:
            # The document contains text addressed to an assistant. It stays study content: fenced in
            # every prompt, never obeyed. The audit records that it happened, not what it said.
            self.audit.record(event="untrusted_instruction_in_source", doc_id=doc_id, reason="contained_as_content")
        return doc_id

    # ================================================================== entry point
    def ask(self, text: str, *, source: InputSource = InputSource.TYPED, references: Optional[list[str]] = None,
            snapshots: Optional[list[ctx.ContextSnapshot]] = None, ocr: Optional[OcrResult] = None,
            student_answer: str = "", confidence: Optional[float] = None) -> StudyResponse:
        t0 = time.perf_counter()
        handled = self._command(text)
        if handled is not None:
            return self._finish(handled, t0)
        if self.quiz and (self.quiz.current or _NEXT_Q.match(text or "") or _STOP_QUIZ.search(text or "")):
            return self._finish(self._quiz_turn(text, confidence), t0)

        req = intent.extract(text, source=source, references=references, registry=self.registry)
        if student_answer:
            req.student_answer = student_answer
            req.privacy = Privacy.PERSONAL
            if "student_answer" in req.missing:
                req.missing.remove("student_answer")
        uncertain: list[str] = []
        if ocr is not None:
            quality = assess(ocr)
            req.input_source, req.privacy = InputSource.CAPTURE, Privacy.SENSITIVE
            if not quality.usable:
                return self._finish(self._simple(req, "I can't read that capture reliably — please retake it closer, "
                                                      "with even light.", needs=["clearer_capture"]), t0)
            req.student_answer = ocr.text()
            uncertain = quality.uncertain
            if "student_answer" in req.missing:
                req.missing.remove("student_answer")
        self._fill_from_session(req)

        # The present screen / video / teacher: decide before anything else looks for material.
        decision = None
        if req.refers_to_screen or ("teacher" in text.lower() and (snapshots or self.context_provider)):
            snaps = snapshots if snapshots is not None else (self.context_provider.snapshots() if self.context_provider else [])
            decision = ctx.decide(req, snaps)
            if decision.need:
                msg = {"capture": "I don't have a current view of that — share the screen or select the part you mean.",
                       "clearer_capture": "The text on screen is too unclear to read reliably — can you zoom in or select it?",
                       "source": "I don't have that material."}.get(decision.need, "I need the material first.")
                return self._finish(self._simple(req, msg, needs=[decision.need]), t0)
            if decision.use is not None and decision.use.kind in ("video", "teacher") and _JUST_SAID.search(text or ""):
                return self._finish(self._just_said(req, decision.use), t0, req)
            if decision.use is not None:
                doc_id = ctx.ingest_snapshot(self.store, decision.use)
                self._audit_doc(doc_id)
                req.references = [doc_id]
                if req.task in (TaskType.EXPLAIN, TaskType.DEFINE, TaskType.SUMMARIZE, TaskType.ANSWER, TaskType.QUOTE):
                    req.source_required = True
        elif req.refers_to_past and self.recall is not None and self.policy.personalization:
            self.recall_calls += 1           # only a past reference may consult personal memory

        resp = self._dispatch(req, uncertain)
        if decision is not None:
            resp.used_memory = False
            resp.audit.append(f"context:{decision.reason}")
        return self._finish(resp, t0, req)

    # ================================================================== dispatch
    def _dispatch(self, req: StudyRequest, uncertain: list[str]) -> StudyResponse:
        if req.task is TaskType.QUIZ:
            return self._start_quiz(req)
        if req.task is TaskType.PLAN:
            return self._plan(req)
        if req.task in (TaskType.EVALUATE, TaskType.CORRECT):
            return self._evaluate(req, uncertain)
        if req.task is TaskType.HINT or (self.sessions.current and self.sessions.current.mode == "hints_only"
                                         and req.task in (TaskType.SOLVE, TaskType.ANSWER)):
            return self._hint(req)
        if req.mode is AnswerMode.LINE_BY_LINE and (req.references or req.source_required):
            return self._line_by_line(req)
        if req.source_required or req.task is TaskType.QUOTE:
            return self._grounded(req)
        if req.task is TaskType.SOLVE or req.mode in (AnswerMode.SHORT, AnswerMode.STEPWISE) or \
                (req.compute and req.task is TaskType.EXPLAIN and not req.topic):
            r = self._solve(req)
            if r is not None:
                return r
        if req.task in (TaskType.REVISE, TaskType.SUMMARIZE, TaskType.FLASHCARDS):
            return self._revise(req)
        if req.task is TaskType.ANSWER:
            return self._exam(req)
        if req.task is TaskType.COMPARE:
            return self._via_brain(req, BrainTask.EXPLANATION, label="Class 10-level comparison (AI-written)")
        return self._explain(req)

    def _just_said(self, req: StudyRequest, snap: ctx.ContextSnapshot) -> StudyResponse:
        """"What did the teacher just say?" — the transcript's last half-minute, cited by time.
        Nothing is paraphrased or invented; an explanation is offered, not assumed."""
        lines = snap.window(before_s=30.0, after_s=3.0) or snap.transcript[-4:]
        resp = self._resp(req, label="From the video transcript")
        if not lines:
            resp.sections.append(Section("", "There's no transcript for that part of the video, so I can't tell "
                                             "what was said.", EN))
            resp.needs.append("transcript")
            return resp
        body = "\n".join(f"[{int(ln.start_s) // 60}:{int(ln.start_s) % 60:02d}] {excerpt(ln.text)}" for ln in lines)
        resp.sections.append(Section("", body, EN))
        start = lines[0].start_s
        resp.citations.append(Citation(chunk_id="", doc_id=snap.doc_id or "video", title=snap.title or "the video",
                                       locator=f"at {int(start) // 60}:{int(start) % 60:02d}",
                                       excerpt=excerpt(lines[-1].text)))
        resp.grounded = True
        resp.follow_up = "Want me to explain that part?"
        return resp

    # ------------------------------------------------------------------ explain / exam
    def _explain(self, req: StudyRequest) -> StudyResponse:
        lang = req.response_language
        card = card_for(req.topic)
        if req.topic == "cumulative_frequency" or re.search(r"(?i)cumulative\s+frequency", req.text):
            return self._cumulative(req)
        if card is None:
            return self._via_brain(req, BrainTask.EXPLANATION, label="Class 10-level explanation (AI-written, not source-checked)")
        resp = self._resp(req, label="Class 10-level explanation (general knowledge)")
        sections, check = modes.understand(card, lang, include_definition=req.detail is not req.detail.BRIEF)
        resp.sections += sections
        for s in sections:
            resp.claims.append(Claim(s.body, SupportKind.GENERAL))
        if req.exam_language is not None:
            resp.sections.append(self._exam_section(card, req.marks or 3, req.exam_language, resp))
            resp.label = "Class 10-level explanation + NCERT-style exam answer"
        resp.follow_up = check
        if req.diagram and card.visual:
            resp.visual = self._visual_for(card.visual, lang)
        self.mastery.record(self._key(req), "explained")
        return resp

    def _exam(self, req: StudyRequest) -> StudyResponse:
        card = card_for(req.topic)
        marks = req.marks or 3
        if card is None:
            resp = self._via_brain(req, BrainTask.EXPLANATION, label="Exam-style answer (AI-written, not source-checked)",
                                   marks=marks, max_words=35 * marks)
            resp.uncertainty.append("No verified marking points for this topic — check it against your textbook.")
            return resp
        resp = self._resp(req, label="NCERT-style exam answer (general Class 10 knowledge, not quoted from NCERT)")
        resp.sections.append(self._exam_section(card, marks, req.response_language, resp))
        scheme = rubrics.scheme_from_card(card, marks)
        resp.verification.append(f"Scoring points covered: {len(scheme.points)} for {marks} mark(s)")
        verify.check(resp, self.store, misconception_ids=card.misconceptions, scheme=scheme, generated=True)
        self.mastery.record(self._key(req), "explained")
        return resp

    def _exam_section(self, card: Card, marks: int, lang: Language, resp: StudyResponse) -> Section:
        sentences = card.exam_answer(marks)
        if marks >= 5 and len(sentences) < marks:
            extra = [p.idea + "." for p in card.points if p.idea + "." not in sentences and
                     all(p.id != pid for pid, _ in card.exam[max(k for k in card.exam if k <= marks)])]
            sentences = sentences + extra[: marks - len(sentences)]
            if len(sentences) < marks:
                resp.uncertainty.append(f"My verified points for this topic support about {len(sentences)} marks; "
                                        f"a {marks}-mark answer usually also needs a diagram or example.")
        formula = ", ".join(card.formulae)
        if lang is not EN:
            reply = self.gateway.submit(BrainRequest(BrainTask.TRANSLATE, resp.request_id, lang, "\n".join(sentences),
                                                     must_include=sentences, privacy=Privacy.PUBLIC))
            resp.brain_calls += 1
            if reply.ok and reply.text.strip():
                return Section(modes.h("exam", lang) + f" ({marks} marks)", _numbered(scrub_for_prompt(reply.text)), lang, "points")
            resp.uncertainty.append({HI: "हिंदी में सत्यापित परीक्षा-उत्तर अभी उपलब्ध नहीं है; नीचे अंग्रेज़ी उत्तर है।",
                                     HG: "Verified exam answer abhi sirf English mein hai."}.get(lang, "Exam answer only in English."))
            lang = EN
        return modes.exam(sentences, marks, lang, formula=formula)

    def _cumulative(self, req: StudyRequest) -> StudyResponse:
        lang = req.response_language
        card = CARDS["cumulative_frequency"]
        nums = [int(n) for n in re.findall(r"\b\d{1,4}\b", req.text)]
        cf = nums if len(nums) >= 3 and math_engine.is_cumulative(nums) else [5, 12, 20, 26, 30]
        rec = math_engine.frequencies_from_cumulative(cf)
        resp = self._resp(req, label="Class 10-level explanation (general knowledge)")
        sections, check = modes.understand(card, lang, include_definition=False)
        resp.sections += sections
        classes = [f"{10 * i}–{10 * (i + 1)}" for i in range(len(cf))]
        table = ["| Class | c.f. (given) | f = c.f. − previous c.f. |", "|---|---|---|"] + [
            f"| {c} | {x} | {w.split('= ', 1)[-1] if '−' in w else x} |" for c, x, w in zip(classes, cf, rec.working)]
        resp.sections.append(Section("Table", "\n".join(table), lang, kind="table"))
        resp.sections.append(Section("Verify by subtraction", "\n".join(rec.working), lang, kind="steps"))
        resp.verification.append("frequencies recovered and re-summed to the last c.f." + (" ✓" if rec.verified else " ✗"))
        resp.follow_up = check
        if req.diagram:
            resp.visual = visuals.table("Cumulative frequency", ["Class", "c.f.", "f"],
                                        [[c, str(x), str(f)] for c, x, f in zip(classes, cf, rec.frequencies)])
        self.mastery.record(self._key(req) or "math.statistics/cumulative_frequency", "explained")
        return resp

    # ------------------------------------------------------------------ grounded
    def _grounded(self, req: StudyRequest) -> StudyResponse:
        docs = req.references or ([self.sessions.current.current_source] if self.sessions.current and
                                  self.sessions.current.current_source else [])
        if not docs and req.source_required:
            docs = list(self.store.docs)
        chunks = self.store.doc_chunks(docs) if docs else []
        resp = self._resp(req, label="From your material")
        if not chunks:
            resp.source_missing = True
            resp.needs.append("source")
            resp.sections.append(Section("", "Which material should I use? Share the page, PDF or selection — I won't "
                                             "answer this from memory and call it the textbook.", req.response_language))
            return resp
        query = _question_part(req)
        hits = Retriever(chunks).search(query, k=3)
        if not hits and len(chunks) <= 2 and req.refers_to_screen:
            hits = [h for h in Retriever(chunks).search(query, k=2, min_coverage=0.0)] or []
            if not hits:   # a short on-screen paragraph: the whole selection is the material
                from .retrieval import Hit
                hits = [Hit(c, 0.0, (), 0.0) for c in chunks[:2]]
        titles = sorted({c.title for c in chunks})
        if not hits:
            resp.source_missing = True
            resp.sections.append(Section("", f"{' / '.join(titles)} doesn't say this — I searched every page I have "
                                             "of it. I can explain it from general Class 10 knowledge instead, "
                                             "clearly labelled as not from your material.", req.response_language))
            return resp
        top = [h.chunk for h in hits]
        resp.citations = [_cite(c, _key_term(query, c)) for c in top[:2]]
        if req.exact_wording or req.task is TaskType.QUOTE:
            c = top[0]
            q = excerpt(best_sentence(c, query)[0] or c.text, "", MAX_EXCERPT)
            resp.sections.append(Section(f"Exact wording ({c.locator() or c.title})", f"“{q}”", req.language))
            resp.claims.append(Claim(q, SupportKind.SOURCE, [resp.citations[0]]))
            if len(c.text) > MAX_EXCERPT:
                resp.uncertainty.append("Quoted a short excerpt only; the full passage is in your copy.")
        else:
            reply = self.gateway.submit(BrainRequest(
                BrainTask.GROUNDED_ANSWER if not req.refers_to_screen else BrainTask.EXPLANATION,
                req.request_id, req.response_language, req.text, sources=top, privacy=req.privacy,
                marks=req.marks))
            resp.brain_calls += 1
            if reply.ok and reply.text.strip() and "NOT_IN_SOURCE" not in reply.text:
                self._absorb_grounded(resp, scrub_for_prompt(reply.text), top, req.response_language)
            elif reply.ok and "NOT_IN_SOURCE" in reply.text:
                resp.source_missing, resp.citations = True, []
                resp.sections.append(Section("", f"{' / '.join(titles)} doesn't answer this.", req.response_language))
                return resp
            else:
                # No brain: answer extractively — the best matching sentence of each hit, cited.
                picked = [(c, *best_sentence(c, query)) for c in top[:2]]
                picked = [(c, s, cov) for c, s, cov in picked if s and (cov >= 0.5 or req.refers_to_screen)]
                if not picked:
                    resp.source_missing, resp.citations = True, []
                    resp.sections.append(Section("", f"{' / '.join(titles)} doesn't say this directly.", req.response_language))
                    return resp
                resp.citations = [_cite(c) for c, _, _ in picked]
                for c, sent, _ in picked:
                    sent = excerpt(sent, "", MAX_EXCERPT)
                    resp.sections.append(Section(f"From {c.locator() or c.title}", sent, req.language))
                    resp.claims.append(Claim(sent, SupportKind.SOURCE, [_cite(c)]))
                resp.uncertainty.append("Answered with the relevant lines from your material (no model available to rephrase).")
        resp.grounded = True
        verify.check(resp, self.store)
        if any(c.suspicious for c in top):
            resp.audit.append("source_contains_instructions:ignored")
        if req.refers_to_screen:
            self.mastery.record(self._key(req), "seen")
        return resp

    def _line_by_line(self, req: StudyRequest) -> StudyResponse:
        chunks = self.store.doc_chunks(req.references) if req.references else []
        resp = self._resp(req, label="Line by line, from your material")
        if not chunks:
            resp.needs.append("source")
            resp.sections.append(Section("", "Show me the lines (select them or share the page) and I'll go through them.",
                                         req.response_language))
            return resp
        c = chunks[0]
        lines = [ln.strip() for ln in re.split(r"\n|(?<=[.!?;।])\s+", c.text) if len(ln.strip()) > 3][:12]
        reply = self.gateway.submit(BrainRequest(BrainTask.EXPLANATION, req.request_id, req.response_language,
                                                 "Explain each numbered line simply, one numbered meaning per line:\n" +
                                                 "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1)),
                                                 sources=[c], privacy=req.privacy))
        resp.brain_calls += 1
        meanings = []
        if reply.ok:
            got = {int(m.group(1)): m.group(2).strip() for m in re.finditer(r"(?m)^\s*(\d+)[.)]\s*(.+)$", scrub_for_prompt(reply.text))}
            meanings = [got.get(i, "") for i in range(1, len(lines) + 1)]
        if not any(meanings):
            meanings = ["(meaning needs a language model — none is connected)"] * len(lines)
            resp.uncertainty.append("Showing the lines and key terms only; simple meanings need a model.")
        resp.sections.append(modes.line_by_line(lines, meanings, req.response_language))
        resp.citations = [_cite(c)]
        resp.grounded = True
        return resp

    def _absorb_grounded(self, resp: StudyResponse, text: str, chunks: list[SourceChunk], lang: Language) -> None:
        by_id = {c.chunk_id: c for c in chunks}
        lines = []
        for sent in re.findall(r".+?(?:[.!?।](?:\s*\[[A-Za-z0-9_-]+:\d+:\d+\])*(?=\s|$)|$)", text.strip(), re.S):
            ids = re.findall(r"\[([A-Za-z0-9_-]+:\d+:\d+)\]", sent)
            clean = re.sub(r"\s*\[[A-Za-z0-9_-]+:\d+:\d+\]", "", sent).strip()
            if not clean:
                continue
            cites = [_cite(by_id[i]) for i in ids if i in by_id]
            kind = SupportKind.SOURCE if cites else SupportKind.GENERAL
            resp.claims.append(Claim(clean, kind, cites))
            lines.append(clean + (f" ({cites[0].locator})" if cites and cites[0].locator else "" if cites else " [general explanation]"))
        resp.sections.append(Section("", " ".join(lines), lang))

    # ------------------------------------------------------------------ solving
    def _solve(self, req: StudyRequest) -> Optional[StudyResponse]:
        lang = req.response_language
        calc = self._science_calc(req.text)
        if calc is not None:
            resp = self._resp(req, label="Calculated and checked")
            if req.mode is AnswerMode.SHORT:
                resp.sections.append(modes.short(f"{calc.target} = {calc.display}", lang))
            else:
                resp.sections.append(modes.stepwise_science(calc, lang))
            resp.claims.append(Claim(f"{calc.target} = {calc.display}", SupportKind.COMPUTED))
            resp.verification += calc.verification + [f"units: {calc.unit or 'none'} ✓"]
            resp.uncertainty += [w for w in calc.warnings]
            return resp
        sol = self._math_solution(req.text)
        if sol is None:
            return None
        resp = self._resp(req, label="Calculated and checked")
        if isinstance(sol, math_engine.Value):
            resp.sections.append(modes.short(str(sol), lang))
            resp.verification.append("evaluated exactly")
            return resp
        if req.mode is AnswerMode.SHORT:
            resp.sections.append(modes.short(f"{sol.required} = {sol.final()}", lang))
        else:
            resp.sections.append(modes.stepwise_math(sol, lang))
        resp.claims.append(Claim(sol.final(), SupportKind.COMPUTED))
        resp.verification += sol.verification
        resp.uncertainty += sol.warnings
        return resp

    def _math_solution(self, text: str):
        for e in equations_in(text):
            e = e.strip()
            m = re.match(r"^\s*([a-z])\s*(?:\^\s*2|²)\s*=\s*([\d\s+*/^().√-]+)$", e)
            try:
                if m:
                    return math_engine.length_from_square(m.group(2), name=m.group(1))
                positive = bool(re.search(r"(?i)\b(?:length|side|diagonal|age|number of|speed)\b", text))
                return math_engine.solve(e, positive=positive)
            except math_engine.MathError:
                continue
        expr = re.search(r"[\d(√][\d\s+\-*/^().√×÷]*[\d)]", text)
        if expr and re.search(r"[+\-*/^×÷√]", expr.group(0)):
            try:
                return math_engine.evaluate(expr.group(0))
            except math_engine.MathError:
                return None
        return None

    def _science_calc(self, text: str):
        found = re.findall(r"(\d+(?:\.\d+)?)\s*(kΩ|Ω|ohms?|kohm|mA|A|V|C|J|W|kW|s|min|h|cm|mm|m)\b", text)
        if len(found) < 2:
            return None
        low = text.lower()
        target = next((sym for word, sym in sorted(_Q_WORDS.items(), key=lambda kv: -len(kv[0]))
                       if re.search(rf"\bfind\b.{{0,30}}\b{word}\b|\b{word}\b.{{0,15}}\?|\bwhat\s+is\s+the\s+{word}\b", low)), None)
        if not target:
            return None
        known: dict[str, str] = {}
        for v, u in found:
            key = science_engine._ALIASES.get(u, u)
            sym = {"kohm": "R", "mA": "I", "kW": "P", "min": "t", "h": "t", "cm": "l", "mm": "l"}.get(key, _UNIT_SYM.get(key))
            if sym and sym not in known:
                known[sym] = f"{v} {u}"
        if "parallel" in low and target in ("R", "Rp"):
            rs = [f"{v} {u}" for v, u in found if science_engine._ALIASES.get(u, u) in ("ohm", "kohm")]
            if len(rs) >= 2:
                return science_engine.calculate("1/Rp=1/R1+1/R2", "Rp", {"R1": rs[0], "R2": rs[1]})
        if "series" in low and target in ("R", "Rp"):
            rs = [f"{v} {u}" for v, u in found if science_engine._ALIASES.get(u, u) in ("ohm", "kohm")]
            if len(rs) >= 2:
                return science_engine.calculate("Rs=R1+R2", "Rs", {"R1": rs[0], "R2": rs[1]})
        if target == "H" and "W" in known:
            known.pop("W")
        for f in science_engine.FORMULAE.values():
            syms = set(f.symbols)
            if target in syms and set(known) >= syms - {target} and syms - {target}:
                try:
                    return science_engine.calculate(f.id, target, {k: known[k] for k in syms - {target}})
                except science_engine.ScienceError:
                    continue
        return None

    # ------------------------------------------------------------------ hints
    def _hint(self, req: StudyRequest) -> StudyResponse:
        # A ladder belongs to a question: its equation, else its topic; "another hint" continues the last one.
        key = "|".join(equations_in(req.text)) or (self._key(req) if self._key(req) else "")
        if not key or (key not in self._hints and _REVEAL.search(req.text)):
            key = self._last_hint_key or key
        if key not in self._hints:
            ladder = None
            sol = self._math_solution(req.text)
            if isinstance(sol, math_engine.Solution):
                ladder = modes.hints_for_solution(sol)
            elif (calc := self._science_calc(req.text)) is not None:
                ladder = modes.hints_for_calculation(calc)
            elif (card := card_for(req.topic)) is not None:
                ladder = modes.hints_for_card(card, req.response_language)
            if ladder is None:
                return self._simple(req, "Tell me the question you're stuck on and I'll give you a first hint.", needs=["question"])
            self._hints[key] = (ladder, 0)
        ladder, level = self._hints[key]
        reveal = bool(_REVEAL.search(req.text))
        level += 1
        self._hints[key] = (ladder, level)
        self._last_hint_key = key
        text, is_answer = ladder.get(level, reveal=reveal)
        resp = self._resp(req, label="Hint" if not is_answer else "Answer")
        resp.sections.append(Section(modes.h("hint", req.response_language) + ("" if is_answer else f" {level}"), text,
                                     req.response_language))
        return resp

    # ------------------------------------------------------------------ evaluation
    def _evaluate(self, req: StudyRequest, uncertain: list[str]) -> StudyResponse:
        answer = req.student_answer
        if not answer and len(equations_in(req.text)) >= 2:
            answer = "\n".join(equations_in(req.text))      # "a student writes d²=800, then d=800"
        if not answer:
            return self._simple(req, "Paste, type or show me your answer and I'll check it.", needs=["student_answer"])
        resp = self._resp(req, label="Answer check")
        lines = [ln.strip() for ln in re.split(r"\n|;|,\s*(?=[a-z]\s*[=²^])|\band\s+(?:then\s+)?(?:writes?|wrote)\b",
                                                answer) if ln.strip()]
        eq_lines = [ln for ln in lines if re.search(r"[a-z]\s*(?:\^\s*2|²)?\s*=", ln.lower())]
        if len(eq_lines) >= 2:
            chk = math_engine.check_steps(eq_lines)
            if not chk.ok:
                resp.sections.append(Section("What's correct", "\n".join(f"Line {i}: {ln}" for i, ln in
                                                                         enumerate(eq_lines[: chk.wrong_line - 1], start=1)), EN))
                resp.sections.append(Section(f"Mistake (line {chk.wrong_line})", chk.explanation, EN))
                if chk.misconception == "forgot_square_root":
                    m = re.search(r"=\s*([\d.]+)", eq_lines[chk.wrong_line - 2])
                    fix = math_engine.length_from_square(m.group(1)) if m else None
                    if fix:
                        resp.sections.append(Section("Corrected", "\n".join(fix.steps + [f"{fix.required} = {fix.final()}"] +
                                                                           [f"Check: {v}" for v in fix.verification]), EN, "steps"))
                    resp.sections.append(Section("Next time", "When you reach d² = k, write d = √k before simplifying.", EN))
                resp.verification.append(f"steps checked deterministically; first wrong line: {chk.wrong_line}")
                resp.claims.append(Claim(chk.explanation, SupportKind.COMPUTED))
                self._mistake(req, chk.misconception)
            else:
                resp.sections.append(Section("Result", "Every step follows from the one before it. ✓", EN))
                resp.verification.append("steps checked deterministically")
                self.mastery.record(self._key(req), "correct")
            resp.follow_up = "Marks: cannot grade reliably without a marking scheme — the working itself is checked above."
            return resp

        card = card_for(req.topic) or _card_from_text(answer)
        scheme = rubrics.scheme_from_card(card, req.marks or 3) if card else None
        ev = rubrics.evaluate(answer, scheme, misconception_ids=card.misconceptions if card else (),
                              uncertain_words=uncertain, card=card)
        lang = req.response_language
        if ev.correct:
            resp.sections.append(Section("What's correct", "\n".join(f"✓ {c}" for c in ev.correct), lang, "points"))
        if ev.inaccurate:
            resp.sections.append(Section("Inaccurate", "\n".join(f"✗ {c}" for c in ev.inaccurate), lang, "points"))
        if ev.missing:
            resp.sections.append(Section("Missing", "\n".join(f"• {c}" for c in ev.missing), lang, "points"))
        unc = [f for f in ev.findings if f.verdict == "uncertain"]
        if uncertain:
            resp.sections.append(Section("Couldn't read clearly", "Words marked uncertain (not counted against you): " +
                                         ", ".join(f"“{w}”" for w in uncertain), lang))
        resp.sections.append(Section("Marks", ev.marks_estimate, lang))
        if ev.corrected:
            resp.sections.append(Section("A stronger version (NCERT-style)", ev.corrected, EN, "points"))
        resp.sections.append(Section("One improvement", ev.next_step or "—", lang))
        resp.follow_up = ev.clarify if unc else ""
        resp.uncertainty.append(f"confidence {ev.confidence:.1f}")
        if card is None:
            resp = self._via_brain(req, BrainTask.EVALUATION, label="Answer check (AI-written, no marking scheme)", base=resp)
        for f in ev.findings:
            if f.verdict == "inaccurate":
                mid = next((m for m in (card.misconceptions if card else ()) if MISCONCEPTIONS[m].correction == f.detail), "")
                self._mistake(req, mid)
        if card and not ev.inaccurate and not ev.missing:
            self.mastery.record(f"{card.chapter}/{card.topic}", "correct")
        return resp

    def _mistake(self, req: StudyRequest, misconception: str) -> None:
        key = self._key(req) or (f"math.triangles/pythagoras" if misconception == "forgot_square_root" else "")
        self.mastery.record(key, "incorrect")
        if misconception:
            self.mastery.misconception(misconception, key)
        if self.sessions.current:
            self.sessions.note_mistake(key, misconception)

    # ------------------------------------------------------------------ revision / quiz / plan
    def _revise(self, req: StudyRequest) -> StudyResponse:
        if req.source_required and (req.references or self.store.docs):
            return self._grounded(req)
        cards = [c for c in CARDS.values() if (req.chapter and c.chapter == req.chapter) or (req.topic and c.topic == req.topic)]
        if not cards and self.sessions.current and self.sessions.current.chapter:
            cards = [c for c in CARDS.values() if c.chapter == self.sessions.current.chapter]
        if not cards:
            return self._simple(req, "Which chapter? I have verified revision notes for Electricity, Light, Metals and "
                                     "non-metals, Triangles and Statistics; for others share your chapter.", needs=["chapter"])
        resp = self._resp(req, label="NCERT-style revision notes (general knowledge)")
        if req.task is TaskType.FLASHCARDS:
            deck = exports.flashcards(cards, req.response_language)
            resp.sections.append(Section("Flashcards", "\n".join(f"Q: {d['front']}\nA: {d['back']}" for d in deck), req.response_language))
        else:
            resp.sections += modes.revision(cards, req.response_language, max_lines=10 if req.detail.value == "brief" else 18)
        return resp

    def _start_quiz(self, req: StudyRequest) -> StudyResponse:
        chapter = req.chapter or (self.sessions.current.chapter if self.sessions.current else "")
        topics = sorted({q.topic for q in BANK if not chapter or q.topic.startswith(chapter + "/")})
        if not topics:
            return self._simple(req, "I don't have practice questions for that chapter yet — Electricity is ready.",
                                needs=["chapter"])
        self.sessions.ensure(req.subject, chapter)
        self.quiz = QuizEngine(self.mastery, topics=topics, difficulty=1)
        q = self.quiz.next()
        resp = self._resp(req, label="Quiz — one question at a time")
        resp.sections.append(Section("Question 1", q, req.response_language))
        resp.follow_up = "Answer when ready — say “hint” if you're stuck."
        return resp

    def _quiz_turn(self, text: str, confidence: Optional[float]) -> StudyResponse:
        req = StudyRequest(text="(quiz turn)", task=TaskType.QUIZ, input_source=InputSource.QUIZ, privacy=Privacy.PERSONAL)
        resp = self._resp(req, label="Quiz")
        qz = self.quiz
        if _STOP_QUIZ.search(text):
            s = qz.summary()
            self.quiz = None
            resp.sections.append(Section("Quiz finished", f"{s['correct']}/{s['asked']} correct.", EN))
            return resp
        if qz.current is None:
            q = qz.next()
            resp.sections.append(Section(f"Question {len(qz.attempts) + 1}", q or "That's all the questions I have.", EN))
            return resp
        if re.fullmatch(r"(?i)\s*hint\s*[.!?]?\s*", text or ""):
            resp.sections.append(Section("Hint", qz.hint(), EN))
            return resp
        topic = qz.current.topic
        g = qz.answer(text, confidence)
        self.metrics.quiz(correct=g.correct, difficulty=qz.difficulty, hints=qz.attempts[-1].hints)
        resp.sections.append(Section("✓" if g.correct else "✗", g.feedback, EN))
        if g.misconception and self.sessions.current:
            self.sessions.note_mistake(topic, g.misconception)
        nxt = qz.next()
        if nxt:
            resp.sections.append(Section(f"Question {len(qz.attempts) + 1}", nxt, EN))
        return resp

    def _plan(self, req: StudyRequest) -> StudyResponse:
        m = _MINUTES.search(req.text)
        minutes = int(m.group(1)) * (60 if m and m.group(2).lower().startswith("h") else 1) if m else 30
        rng = _CHAPTER_RANGE.search(req.text)
        if rng:
            chapters = self.registry.by_number(range(int(rng.group(1)), int(rng.group(2)) + 1), req.subject or "science")
        elif req.chapter:
            chapters = [self.registry.chapter(req.chapter)]
        elif self.sessions.current and self.sessions.current.chapter:
            chapters = [self.registry.chapter(self.sessions.current.chapter)]
        else:
            chapters = [c for c in self.registry.chapters() if c.depth == "deep" and (not req.subject or c.subject == req.subject)]
        chapters = [c for c in chapters if c]
        tomorrow = bool(re.search(r"(?i)\btomorrow\b|\bkal\b", req.text))
        p = revision_plan(minutes, chapters=chapters, mastery=self.mastery, exam_in_days=1 if tomorrow else None,
                          weak_only=bool(re.search(r"(?i)\bweak\b", req.text)),
                          formula_run=bool(re.search(r"(?i)formula\s+run|last[\s-]second", req.text)),
                          registry=self.registry)
        self.last_plan = p                   # its reminder_request is offered through the host's approvals
        resp = self._resp(req, label="Revision plan")
        resp.sections.append(Section(f"{p.total()} minutes", p.text(), req.response_language, "steps"))
        if rng:
            resp.uncertainty.append("Chapter numbers follow the 2023–24 NCERT order as I have it — check against your book.")
        resp.follow_up = "Want me to start with the first block now? I haven't put anything on your calendar."
        resp.audit.append("plan:no_calendar_action")
        return resp

    # ------------------------------------------------------------------ commands
    def _command(self, text: str) -> Optional[StudyResponse]:
        for name, rx in _SESSION:
            m = rx.search(text or "")
            if not m:
                continue
            req = StudyRequest(text="(command)", task=TaskType.SESSION)
            r = self._resp(req, label="Study session")
            say = lambda s: r.sections.append(Section("", s, EN))  # noqa: E731
            sm = self.sessions
            if name == "start":
                subj = intent.subject_of(text)
                sm.start(subject=subj)
                say(f"Started a {subj or 'study'} session. What are we studying?")
            elif name == "studying":
                ch, _ = self.registry.find(m.group("what"))
                s = sm.ensure(ch.subject if ch else "", ch.id if ch else "")
                if ch:
                    s.chapter, s.subject = ch.id, ch.subject
                    sm._save()
                say(f"Okay — {ch.title if ch else m.group('what').strip(' .')}."
                    + ("" if ch else " (Not in my installed syllabus data — share the chapter and I'll work from it.)"))
            elif name == "explain_then_quiz":
                sm.ensure().mode = "explain_then_quiz"
                sm._save()
                say("I'll explain first, then quiz you.")
            elif name == "hints_only":
                sm.ensure().mode = "hints_only"
                sm._save()
                say("Only hints from now on — say “show the answer” when you want it.")
            elif name == "pause":
                sm.pause()
                say("Paused. Say “continue from where we stopped” when you're back.")
            elif name == "resume":
                s = sm.resume()
                say(f"Back to {self._chapter_title(s.chapter) if s and s.chapter else 'studying'}"
                    + (f"; last topic: {s.last_topic.split('/')[-1].replace('_', ' ')}." if s and s.last_topic else ".")
                    if s else "There's no earlier session to continue.")
            elif name == "wrong":
                s = sm.current or sm.last
                mis = self.mastery.active_misconceptions()
                if not mis and not (s and s.mistakes):
                    say("No mistakes recorded in this session.")
                else:
                    say("\n".join(f"• {MISCONCEPTIONS[x.id].label} — {MISCONCEPTIONS[x.id].correction}" for x in mis) or
                        f"{len(s.mistakes)} mistake(s), in: " + ", ".join(sorted({x['topic'] for x in s.mistakes})))
            elif name == "end":
                s = sm.end()
                say("Session ended." + (f" Summary: {s.summary()}" if s else ""))
            elif name == "summary":
                s = sm.current or sm.last
                say(str(s.summary()) if s else "No session yet.")
            elif name == "progress":
                v = self.mastery.view()
                lines = [f"{k.split('/')[-1].replace('_', ' ')}: {d['estimate']:.0%} ({d['evidence']} answers)"
                         for k, d in v["topics"].items()]
                say("\n".join(lines) or "Nothing recorded yet.")
                r.sections.append(Section("", v["note"], EN, "note"))
            elif name == "reset":
                ch, tp = self.registry.find(m.group("what"))
                key = f"{ch.id}/{tp.id}" if ch and tp else (ch.id if ch else "")
                done = bool(key) and self.mastery.reset_topic(key)
                if ch and not tp:
                    done = any([self.mastery.reset_topic(k) for k in list(self.mastery.topics) if k.startswith(ch.id + "/")]) or done
                say("Reset." if done else "Nothing recorded for that yet.")
            elif name == "delete":
                self.mastery.delete_all()
                self.sessions.delete()
                self.quiz = None
                self._hints.clear()
                say("Deleted your study history: progress, mistakes and sessions.")
            elif name == "personal_off":
                self.mastery.set_personalization(False)
                self.policy.personalization = False
                say("Personalisation is off — I won't record or use your progress.")
            elif name == "personal_on":
                self.mastery.set_personalization(True)
                self.policy.personalization = True
                say("Personalisation is on.")
            r.audit.append(f"command:{name}")
            return r
        return None

    # ------------------------------------------------------------------ brain fallback
    def _via_brain(self, req: StudyRequest, kind: BrainTask, *, label: str, marks: Optional[int] = None,
                   max_words: int = 0, base: Optional[StudyResponse] = None) -> StudyResponse:
        resp = base or self._resp(req, label=label)
        reply = self.gateway.submit(BrainRequest(kind, req.request_id, req.response_language, req.text,
                                                 student_answer=req.student_answer, marks=marks, max_words=max_words,
                                                 privacy=req.privacy))
        resp.brain_calls += 1
        if not reply.ok:
            if base is None:
                why = "privacy settings keep your material on this machine" if reply.reason == "privacy_blocked" else \
                      "no language model is connected right now"
                resp.sections.append(Section("", f"I can't answer this one reliably offline ({why}), and I don't have "
                                                 "verified notes for it. Share your chapter or try again later.",
                                             req.response_language))
                resp.needs.append("brain")
            return resp
        resp.label = label
        text = scrub_for_prompt(reply.text)
        resp.sections.append(Section("", text, req.response_language))
        resp.claims.append(Claim(text, SupportKind.GENERAL))
        if self.registry.coverage_of(req.text) is Coverage.UNVERIFIED and req.subject == "":
            resp.uncertainty.append("This isn't in my installed syllabus data — check it's part of your syllabus.")
        verify.check(resp, self.store, language=req.response_language, generated=True,
                     misconception_ids=[m for c in CARDS.values() if c.topic == req.topic for m in c.misconceptions])
        return resp

    # ------------------------------------------------------------------ helpers
    def _resp(self, req: StudyRequest, label: str = "") -> StudyResponse:
        return StudyResponse(request_id=req.request_id, task=req.task, mode=req.mode, language=req.response_language,
                             label=label)

    def _simple(self, req: StudyRequest, text: str, needs: Optional[list[str]] = None) -> StudyResponse:
        r = self._resp(req)
        r.sections.append(Section("", text, req.response_language))
        r.needs = list(needs or [])
        return r

    def _key(self, req: StudyRequest) -> str:
        return f"{req.chapter}/{req.topic}" if req.chapter and req.topic else ""

    def _chapter_title(self, cid: str) -> str:
        ch = self.registry.chapter(cid)
        return ch.title if ch else cid

    def _fill_from_session(self, req: StudyRequest) -> None:
        s = self.sessions.current
        if not s:
            return
        if not req.topic and s.last_topic and _DEICTIC.search(req.text):
            req.chapter, _, req.topic = s.last_topic.partition("/")
            ch = self.registry.chapter(req.chapter)
            req.subject = ch.subject if ch else req.subject
            if "topic" in req.missing:
                req.missing.remove("topic")
        if not req.chapter and s.chapter and req.task in (TaskType.QUIZ, TaskType.REVISE, TaskType.PLAN, TaskType.FLASHCARDS):
            req.chapter = s.chapter

    def _visual_for(self, kind: str, lang: Language) -> Optional[visuals.TeachingVisualRequest]:
        builders = {"ray_diagram": visuals.ray_diagram, "circuit": visuals.circuit,
                    "triangle": lambda: visuals.right_triangle(20, 20)}
        b = builders.get(kind)
        if not b:
            return None
        v = b()
        v.language = str(lang)
        if self.renderer is not None:
            self.renderer.render(v)
        return v

    def _finish(self, resp: StudyResponse, t0: float, req: Optional[StudyRequest] = None) -> StudyResponse:
        # Whatever a model wrote, no secret or private detail leaves in a response.
        for s in resp.sections:
            s.body = scrub_for_prompt(s.body)
        if req is not None:
            if req.diagram and resp.visual is None:
                card = card_for(req.topic)
                if card and card.visual:
                    resp.visual = self._visual_for(card.visual, req.response_language)
            if resp.visual is not None and visuals.validate(resp.visual):
                resp.audit.append("visual:invalid_dropped")
                resp.visual = None
            if self.sessions.current and (key := self._key(req)):
                self.sessions.note_topic(key)
            self.metrics.request(request_id=req.request_id, task=str(req.task), mode=str(req.mode),
                                 language=str(req.response_language), latency_ms=(time.perf_counter() - t0) * 1000,
                                 brain_calls=resp.brain_calls, grounded=resp.grounded, source_missing=resp.source_missing,
                                 chunks=len(resp.citations))
        return resp


# ---------------------------------------------------------------------------------- helpers
def _cite(c: SourceChunk, around: str = "") -> Citation:
    return Citation(c.chunk_id, c.doc_id, c.title, c.locator(), excerpt(c.text, around, 120))


_MATH_ATOM = r"(?:(?<![a-z])[a-z](?![a-z])|[0-9²³√π().^*/+\-×÷ ])"
_EQUATION = re.compile(rf"{_MATH_ATOM}+={_MATH_ATOM}+")


def equations_in(text: str) -> list[str]:
    """Equations embedded in a sentence: "a student writes d^2=800 and then d=800" → two."""
    out = []
    for m in _EQUATION.finditer((text or "").lower()):
        e = m.group(0).strip(" .,")
        if re.search(r"\d|[a-z]", e.split("=")[0]) and re.search(r"\d|[a-z]", e.split("=")[1]):
            out.append(e)
    return out


def _question_part(req: StudyRequest) -> str:
    q = re.sub(r"(?i)\b(?:according\s+to\s+(?:this|the|my|these|those)\s+\w+|use\s+only\s+(?:this|the|my|these)\s+\w+|"
               r"what\s+does\s+(?:ncert|the\s+\w+)\s+say\s+about|give\s+(?:me\s+)?the\s+exact\s+(?:textbook\s+)?answer|"
               r"from\s+(?:this|the|my)\s+(?:pdf|chapter|page|notes|book))\b", " ", req.question or req.text)
    return q.strip(" ,:?.") or req.text


def _key_term(query: str, chunk: SourceChunk) -> str:
    from .retrieval import terms
    for t in sorted(set(terms(query)), key=len, reverse=True):
        if t in chunk.text.lower():
            return t
    return ""


def _numbered(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if all(re.match(r"^\d+[.)]", ln) for ln in lines):
        return "\n".join(lines)
    return "\n".join(f"{i}. {re.sub(r'^[-•]\s*', '', ln)}" for i, ln in enumerate(lines, start=1))


def _card_from_text(text: str) -> Optional[Card]:
    ch, tp = REGISTRY.find(text)
    return card_for(tp.id) if tp else None
