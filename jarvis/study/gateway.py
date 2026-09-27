"""The one door to a language model: ``StudyBrainGateway``.

The Study Companion never talks to Groq, Gemini, OpenAI, Anthropic, OpenRouter or Ollama. It
submits a typed ``BrainRequest`` — what kind of work, in which language, with which source
chunks (fenced as untrusted), which points must appear, and how private the material is — and
gets a ``BrainReply`` back. Choosing a provider, a model, keys, retries and context compaction is
the Daily Brain capability router's job (branch ``feat/jarvis-daily-brain``). Until that lands:

* ``FakeStudyGateway`` — deterministic, for tests and for development.
* ``UnavailableGateway`` — what production uses before integration: every call says "no brain",
  and the companion answers from its verified offline knowledge and engines, and says so.

``GuardedGateway`` wraps any gateway and is always used: it scrubs secrets and contact details,
fences document text, refuses to send personal/sensitive material to a cloud route the privacy
policy forbids, and counts calls. The integration adapter is sketched in docs/STUDY_COMPANION.md
("Daily Brain integration contract").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from .privacy import PrivacyPolicy, fence, scrub_for_prompt
from .types import Language, Privacy, SourceChunk, _Str


class BrainTask(_Str):
    EXPLANATION = "explanation"
    GROUNDED_ANSWER = "grounded_answer"
    EVALUATION = "answer_evaluation"
    STEPWISE = "stepwise_solution"
    QUIZ = "quiz_generation"
    HINT = "hint_generation"
    SUMMARY = "summarization"
    TRANSLATE = "multilingual_transformation"


# What the router should prefer for each kind. Advisory: the router owns the decision.
CAPABILITY = {
    BrainTask.EXPLANATION: "reasoning", BrainTask.GROUNDED_ANSWER: "reasoning",
    BrainTask.EVALUATION: "reasoning", BrainTask.STEPWISE: "reasoning",
    BrainTask.QUIZ: "fast", BrainTask.HINT: "fast", BrainTask.SUMMARY: "long_context",
    BrainTask.TRANSLATE: "multilingual",
}

# Fixed instructions per kind. The model gets these, not anything from a document.
INSTRUCTIONS = {
    BrainTask.EXPLANATION: "Explain for a Class 10 student. Intuition first, one everyday example, then the precise "
                           "definition. Include every point listed under MUST_INCLUDE. End with one short question "
                           "that checks understanding.",
    BrainTask.GROUNDED_ANSWER: "Answer ONLY from the fenced SOURCES. After each sentence put the chunk id in square "
                               "brackets. If the sources do not contain the answer, reply exactly NOT_IN_SOURCE.",
    BrainTask.EVALUATION: "Compare the student's answer with the scoring points. For each point say present, missing "
                          "or inaccurate, quoting the student's line number. Do not penalise wording differences or "
                          "words marked [uncertain?].",
    BrainTask.STEPWISE: "Solve step by step: given, required, formula, substitution, calculation, units, final "
                        "answer. Use Class 10 methods only.",
    BrainTask.QUIZ: "Write one question at the requested difficulty, with its answer and the misconception each "
                    "wrong option tests. Do not reuse the listed recent questions.",
    BrainTask.HINT: "Give only the smallest useful next step. Do not reveal the answer.",
    BrainTask.SUMMARY: "Summarise the fenced SOURCES for revision. Cite chunk ids. Do not add facts not in them.",
    BrainTask.TRANSLATE: "Rewrite in the target language, keeping scientific terms students use in English. "
                         "Hinglish means Latin letters; Hindi means simple Devanagari, not Sanskritised.",
}


@dataclass
class BrainRequest:
    kind: BrainTask
    request_id: str
    language: Language
    question: str
    instructions: str = ""
    must_include: list[str] = field(default_factory=list)
    sources: list[SourceChunk] = field(default_factory=list)
    student_answer: str = ""
    max_words: int = 0
    marks: Optional[int] = None
    privacy: Privacy = Privacy.PUBLIC
    capability: str = ""
    recent: list[str] = field(default_factory=list)   # e.g. recent quiz questions, to avoid repeats

    def prompt(self) -> str:
        """The rendered prompt. Document text only ever appears fenced."""
        parts = [self.instructions or INSTRUCTIONS[self.kind], f"LANGUAGE: {self.language}"]
        if self.marks:
            parts.append(f"MARKS: {self.marks}")
        if self.max_words:
            parts.append(f"MAX_WORDS: {self.max_words}")
        if self.must_include:
            parts.append("MUST_INCLUDE:\n- " + "\n- ".join(self.must_include))
        if self.recent:
            parts.append("RECENT (do not repeat):\n- " + "\n- ".join(self.recent))
        if self.sources:
            parts.append("SOURCES:\n" + "\n".join(
                f"[{c.chunk_id}] ({c.locator() or 'no page'})\n" + fence(c.text, "source") for c in self.sources))
        if self.student_answer:
            parts.append("STUDENT_ANSWER:\n" + fence(self.student_answer, "student answer"))
        parts.append("QUESTION:\n" + fence(self.question, "question"))
        return "\n\n".join(parts)


@dataclass
class BrainReply:
    ok: bool
    text: str = ""
    route: str = ""                  # "local" | "cloud" | "" (for the audit, not shown)
    reason: str = ""                 # why not ok: "unavailable", "privacy_blocked", …


class StudyBrainGateway(Protocol):
    cloud: bool                      # whether this gateway may route off the machine

    def submit(self, req: BrainRequest) -> BrainReply: ...


class UnavailableGateway:
    cloud = False

    def submit(self, req: BrainRequest) -> BrainReply:
        return BrainReply(False, reason="unavailable")


class FakeStudyGateway:
    """Deterministic stand-in. Scripted replies by (kind, substring of question); otherwise a
    reply built from ``must_include`` (or NOT_IN_SOURCE for grounded requests with no sources)."""

    def __init__(self, script: Optional[dict] = None, cloud: bool = False,
                 builder: Optional[Callable[[BrainRequest], str]] = None) -> None:
        self.script = dict(script or {})
        self.cloud = cloud
        self.builder = builder
        self.calls: list[BrainRequest] = []
        self.prompts: list[str] = []

    def submit(self, req: BrainRequest) -> BrainReply:
        self.calls.append(req)
        self.prompts.append(req.prompt())
        for (kind, needle), text in self.script.items():
            if kind == req.kind and needle.lower() in req.question.lower():
                return BrainReply(True, text, "cloud" if self.cloud else "local")
        if self.builder:
            return BrainReply(True, self.builder(req), "cloud" if self.cloud else "local")
        if req.kind is BrainTask.GROUNDED_ANSWER or (req.kind is BrainTask.EXPLANATION and req.sources):
            # Behave like a faithful model: the best-supported sentence, cited — or admit absence.
            from .retrieval import best_sentence
            best = max(((c, *best_sentence(c, req.question)) for c in req.sources), key=lambda t: t[2], default=None)
            if best is None or (best[2] < 0.6 and req.kind is BrainTask.GROUNDED_ANSWER):
                return BrainReply(True, "NOT_IN_SOURCE", "local")
            c, sent, _ = best
            if not sent:        # "explain this paragraph": nothing to match, so explain from its opening
                sent = re.split(r"(?<=[.!?])\s+", c.text.strip())[0]
            sent = " ".join(sent.split()).rstrip(".") + "."
            return BrainReply(True, f"{sent} [{c.chunk_id}]", "local")
        body = " ".join(p.rstrip(".") + "." for p in req.must_include) or f"({req.kind} for: {req.question[:60]})"
        return BrainReply(True, body, "cloud" if self.cloud else "local")


class GuardedGateway:
    """Privacy and hygiene around any gateway. Always the one the companion holds."""

    def __init__(self, inner: StudyBrainGateway, policy: Optional[PrivacyPolicy] = None) -> None:
        self.inner = inner
        self.policy = policy or PrivacyPolicy()
        self.calls = 0
        self.blocked = 0

    @property
    def cloud(self) -> bool:
        return bool(getattr(self.inner, "cloud", False))

    def submit(self, req: BrainRequest) -> BrainReply:
        if not self.policy.may_send(req.privacy, self.cloud):
            self.blocked += 1
            return BrainReply(False, reason="privacy_blocked")
        req.question = scrub_for_prompt(req.question)
        req.student_answer = scrub_for_prompt(req.student_answer)
        req.must_include = [scrub_for_prompt(p) for p in req.must_include]
        req.capability = req.capability or CAPABILITY[req.kind]
        req.instructions = req.instructions or INSTRUCTIONS[req.kind]
        self.calls += 1
        try:
            reply = self.inner.submit(req)
        except Exception:            # a gateway failure is "no brain", never a crash mid-lesson
            return BrainReply(False, reason="gateway_error")
        return reply
