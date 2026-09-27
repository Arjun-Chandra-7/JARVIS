"""Deterministic study-intent extraction: text → ``StudyRequest``, no model involved.

"three-mark answer", "only final answer", "give steps", "quiz me", "explain in Hinglish",
"check my answer" — phrases like these are recognised by rules, because a model call to notice
the obvious costs a second of latency and can be wrong in ways a rule cannot. The model is
reserved for writing, and even then only through the gateway.

The one principle: **do not force a doubt into an exam answer.** Exam mode switches on only when
the student asks for marks, an exam/board answer or formal wording. "Why is current opposite to
electron flow?" is a doubt and gets an explanation.
"""
from __future__ import annotations

import re
from typing import Optional

from . import languages
from .curriculum import REGISTRY, Registry, subject_of
from .types import (AnswerMode, ContextRef, Detail, InputSource, Language, OutputFormat, Privacy,
                    StudyRequest, TaskType)

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6,
    "१": 1, "२": 2, "३": 3, "४": 4, "५": 5,
}
_N = r"(?P<n>\d|one|two|three|four|five|six|ek|teen|char|chaar|paanch|panch|एक|दो|तीन|चार|पाँच|पांच|छह|[१२३४५])"
_MARKS = re.compile(
    rf"(?i)(?<![A-Za-z0-9]){_N}\s*[- ]?\s*(?:marks?\b|marker\b|number\s*(?:ka|ke|wala|wali)?\b|अंकों|अंक|नंबर)")
_LONG = re.compile(r"(?i)\b(?:long[\s-]answer|long\s+question|detailed\s+answer|essay[\s-]type)\b|दीर्घ\s*उत्तर")
_VSA = re.compile(r"(?i)\b(?:very\s+short\s+answer|one[\s-]word|one[\s-]line|in\s+one\s+sentence)\b")

_R = lambda p: re.compile(p, re.I)   # noqa: E731
RULES: list[tuple[str, re.Pattern]] = [
    ("session", _R(r"\b(?:start|begin|end|stop|pause|resume|continue)\b.{0,30}\b(?:study|studying|session|padhai)\b|"
                   r"\bwe(?:'re| are)\s+studying\b|\bcontinue\s+from\s+where\b|\bpause\s+studying\b|"
                   r"\bwhat\s+did\s+i\s+get\s+wrong\b|\bend\s+the\s+session\b")),
    ("quiz", _R(r"\bquiz\s+me\b|\btest\s+me\b|\bask\s+me\s+(?:some\s+|a\s+few\s+)?questions\b|\brapid[\s-]fire\b|"
                r"\bmujhse\s+(?:sawal|questions?)\s+pucho\b|\bquiz\b")),
    ("evaluate", _R(r"\bcheck\s+(?:my|this|whether|if)\b|\bis\s+(?:my|this)\s+(?:answer|solution|working)\s+"
                    r"(?:correct|right|wrong)\b|\bmark\s+my\b|\bgrade\s+my\b|\bhow\s+many\s+marks\s+would\b|"
                    r"\bsahi\s+hai\s+(?:ya|kya)\b|\bgalat\s+(?:hai|kya)\b|\bjaanch\b|\bजाँच\b|\bजांच\b")),
    ("correct", _R(r"\b(?:correct|fix|improve|rewrite)\s+my\s+(?:answer|solution|paragraph)\b")),
    ("hint", _R(r"\bhint\b|\bclue\b|\bdon'?t\s+(?:tell|give)\s+(?:me\s+)?the\s+(?:whole\s+|full\s+)?answer\b|"
                r"\bonly\s+(?:give\s+)?hints\b|\bsmall\s+help\b|\bishaara\b")),
    ("plan", _R(r"\bi\s+have\s+\d+\s*(?:min|minutes|hours?|hrs?)\b|\b(?:test|exam|paper)\s+is\s+(?:tomorrow|today|on)\b|"
                r"\bplan\b.{0,20}\brevision\b|\brevision\s+plan\b|\bstudy\s+plan\b|\bplan\s+this\s+week|"
                r"\brevise\s+(?:chapters?|my\s+weak)\b|\bonly\s+revise\b|\bformula\s+run\b")),
    ("revise", _R(r"\blast[\s-]minute\b|\bquick\s+revision\b|\brevision\s+notes\b|\bformula\s+(?:run|sheet|list)\b|"
                  r"\blast[\s-]second\b|\brevise\b|\brecap\b")),
    ("flashcards", _R(r"\bflash\s*cards?\b")),
    ("summarize", _R(r"\bsummar(?:y|ise|ize)\b|\bsaar\b|\bसारांश\b|\bgist\b")),
    ("compare", _R(r"\bcompare\b|\bdifference\s+between\b|\bdistinguish\b|\b(?:vs\.?|versus)\b|\bantar\b|\bअंतर\b")),
    ("prove", _R(r"\bprove\s+that\b|\bshow\s+that\b|\bprove\b")),
    ("derive", _R(r"\bderive\b|\bderivation\b")),
    ("define", _R(r"\bdefine\b|\bdefinition\s+of\b|\bparibhasha\b|\bपरिभाषा\b")),
    ("solve", _R(r"\bsolve\b|\bcalculate\b|\bfind\s+(?:the\s+)?(?:equivalent\s+|total\s+|net\s+)?(?:value|length|resistance|current|area|mean|median|"
                 r"roots?|distance|sum|nth|power|focal)\b|\bhow\s+much\b|\bnikalo\b")),
    ("diagram", _R(r"\bdiagram\b|\bdraw\b|\bsketch\b|\bshow\s+(?:this|it|me)\s+with\s+a\b|\bray\s+diagram\b|"
                   r"\bvisuali[sz]e\b|\bchitra\b|\bचित्र\b")),
    ("explain", _R(r"\bexplain\b|\bwhy\b|\bhow\s+(?:does|do|is|come)\b|\bsamjha|\bsamajh\b|\bdidn'?t\s+understand\b|"
                   r"\bdon'?t\s+understand\b|\bkyun\b|\bkyu\b|\bkaise\b|\bक्यों\b|\bसमझा|\bwhat\s+is\b|\bmatlab\b|"
                   r"\bmeaning\b|\bhow\s+do\s+i\s+know\b")),
]
_STEPS = _R(r"\bstep[\s-]*by[\s-]*step\b|\bgive\s+(?:me\s+)?(?:the\s+)?steps\b|\bstepwise\b|\bwith\s+steps\b|\bsteps\s+ke\s+saath\b")
_FINAL_ONLY = _R(r"\bonly\s+(?:the\s+)?final\s+answer\b|\bjust\s+the\s+answer\b|\bfinal\s+answer\s+only\b|\bsirf\s+answer\b")
_LINE_BY_LINE = _R(r"\bline[\s-]by[\s-]line\b|\bbhavarth\b|भावार्थ|\beach\s+line\b|\bstanza\b")
_EXAM = _R(r"\bexam(?:[\s-](?:answer|ready|style|wording|mode))?\b|\bboard\s+(?:answer|exam)\b|\bformal\b|"
           r"\bhow\s+to\s+write\s+(?:it|this)\s+in\s+(?:the\s+)?exam\b|\bpariksha\b|परीक्षा")
_SIMPLE = _R(r"\bsimple\b|\beasy\b|\basaan\b|\bsimply\b|\blike\s+i'?m\b|\bdidn'?t\s+understand\b|\bsamjha")
_BRIEF = _R(r"\bshort\b|\bbrief\b|\bquick(?:ly)?\b|\bin\s+short\b|\bchhota\b|\bone[\s-]line\b")
_DETAILED = _R(r"\bin\s+detail\b|\bdetailed\b|\bthorough\b|\bvistar\b")
_SOURCE = _R(r"\baccording\s+to\s+(?:this|the|my|these|those|your)\b|\bwhat\s+does\s+(?:ncert|the\s+(?:book|textbook|chapter|pdf))\s+say\b|"
             r"\buse\s+only\s+(?:this|the|my|these)\b|\bfrom\s+(?:this|the|my|these)\s+(?:pdf|chapter|page|notes|book|textbook)\b|"
             r"\bin\s+(?:this|the)\s+(?:pdf|chapter|passage)\b|\bas\s+per\s+(?:the\s+)?(?:book|ncert|textbook)\b")
_EXACT = _R(r"\bexact(?:ly)?\s+(?:textbook\s+|ncert\s+|book\s+)?(?:answer|words?|wording|line)\b|\bwhat\s+is\s+written\b|"
            r"\bword\s+for\s+word\b|\bverbatim\b|\bquote\b")
_SCREEN = _R(r"\bon\s+(?:my|the)\s+screen\b|\bcurrently\s+on\b|\bthis\s+(?:paragraph|page|passage|question|diagram|video|slide)\b|"
             r"\bwhat\s+i(?:'m| am)\s+(?:looking\s+at|reading|watching)\b|\bselected\b|\bhighlighted\b|\bscreen\s+pe\b")
_TEACHER_NOW = _R(r"\bteacher\s+(?:just|has\s+just)\s+(?:said|explained|told)\b|\bwhat\s+(?:sir|ma'?am|the\s+teacher)\s+just\b")
_PAST = _R(r"\b(?:earlier|yesterday|last\s+(?:time|class|week)|previously|pichhli\s+baar|kal)\b")
_SPOKEN = _R(r"\bsay\s+it\b|\bspeak\b|\bout\s+loud\b|\bbolke\b|\btell\s+me\s+aloud\b")
_TABLE = _R(r"\btable\b|\btabular\b")
_MATHY = re.compile(r"(?:\d\s*[+\-*/^=×÷]\s*\d|[a-z]\s*\^\s*\d|[a-z]²|√|\bsqrt\b|=\s*-?\d)", re.I)
_Q_SPLIT = re.compile(r"(?is)(?:^|\n)\s*(?:q(?:uestion)?\s*[:.)-])\s*(?P<q>.+?)(?:\n\s*(?:my\s+)?(?:answer|ans|solution|a)\s*[:.)-]\s*(?P<a>.+))?$")
_ANS_AFTER = re.compile(r"(?is)(?:my\s+(?:answer|solution|working)\s*(?:is|was)?\s*[:\-]|i\s+wrote\s*[:\-]?)\s*(?P<a>.+)$")

_LANG_PHRASE = re.compile(
    r"(?i)\b(?:in|into)\s+(?:simple\s+|easy\s+|plain\s+|pure\s+|formal\s+)?(?:hindi|english|hinglish)\b|"
    r"\b(?:hindi|english|hinglish)\s+(?:mein|me|main)\b|(?:हिंदी|हिन्दी|अंग्रेज़ी|अंग्रेजी)\s*में")

_PRIORITY = ["session", "plan", "quiz", "correct", "evaluate", "hint", "flashcards", "revise", "summarize",
             "compare", "prove", "derive", "solve", "define", "diagram", "explain"]
_TASK = {"session": TaskType.SESSION, "plan": TaskType.PLAN, "quiz": TaskType.QUIZ, "correct": TaskType.CORRECT,
         "evaluate": TaskType.EVALUATE, "hint": TaskType.HINT, "flashcards": TaskType.FLASHCARDS,
         "revise": TaskType.REVISE, "summarize": TaskType.SUMMARIZE, "compare": TaskType.COMPARE,
         "prove": TaskType.PROVE, "derive": TaskType.DERIVE, "solve": TaskType.SOLVE, "define": TaskType.DEFINE,
         "diagram": TaskType.DIAGRAM, "explain": TaskType.EXPLAIN}


def marks_in(text: str) -> Optional[int]:
    m = _MARKS.search(text or "")
    if m:
        n = m.group("n") or ""
        return int(n) if n.isdigit() else _NUM_WORDS.get(n.lower(), _NUM_WORDS.get(n))
    if _LONG.search(text or ""):
        return 5
    if _VSA.search(text or ""):
        return 1
    return None


def extract(text: str, *, source: InputSource = InputSource.TYPED, context: Optional[ContextRef] = None,
            references: Optional[list[str]] = None, registry: Registry = REGISTRY,
            mastery: Optional[dict] = None) -> StudyRequest:
    text = (text or "").strip()
    req = StudyRequest(text=text, input_source=source, context=context, references=list(references or []))
    fired = {name for name, rx in RULES if rx.search(text)}
    req.signals = sorted(fired)

    # --- language
    lp = languages.plan(text)
    req.language, req.response_language, req.exam_language = lp.written_in, lp.respond_in, lp.exam_in
    req.signals += lp.signals

    # --- the question / the student's answer, when both are in the message
    m = _Q_SPLIT.search(text)
    if m and m.group("a"):
        req.question, req.student_answer = m.group("q").strip(), m.group("a").strip()
    else:
        a = _ANS_AFTER.search(text)
        if a:
            req.student_answer = a.group("a").strip()
            req.question = text[: a.start()].strip(" :-\n")

    # --- task
    task_key = next((k for k in _PRIORITY if k in fired), "")
    if task_key == "diagram" and "explain" in fired:
        task_key = "explain"                 # "explain X with a diagram" is an explanation that draws
    req.task = _TASK[task_key] if task_key else TaskType.EXPLAIN
    if not task_key and _MATHY.search(text):
        req.task = TaskType.SOLVE
    req.marks = marks_in(text)
    if req.marks and req.task in (TaskType.EXPLAIN, TaskType.DEFINE, TaskType.DIAGRAM):
        req.task = TaskType.ANSWER           # "a three-mark answer: why do …" is a written answer
    if _EXACT.search(text):
        req.exact_wording = req.source_required = True
        if req.task in (TaskType.EXPLAIN, TaskType.DEFINE):
            req.task = TaskType.QUOTE if re.search(r"(?i)what\s+is\s+written|quote|word\s+for\s+word", text) else TaskType.ANSWER
    if _SOURCE.search(text):
        req.source_required = True

    # --- mode
    if req.task is TaskType.HINT:
        req.mode = AnswerMode.HINT
    elif _LINE_BY_LINE.search(text):
        req.mode = AnswerMode.LINE_BY_LINE
    elif _FINAL_ONLY.search(text):
        req.mode = AnswerMode.SHORT
    elif _STEPS.search(text) or req.task in (TaskType.SOLVE, TaskType.DERIVE, TaskType.PROVE):
        req.mode = AnswerMode.STEPWISE
    elif req.task is TaskType.COMPARE:
        req.mode = AnswerMode.COMPARE
    elif req.task in (TaskType.REVISE, TaskType.FLASHCARDS):
        req.mode = AnswerMode.REVISION
    elif req.marks or _EXAM.search(text):
        req.mode = AnswerMode.EXAM
    else:
        req.mode = AnswerMode.UNDERSTAND
    req.exam_mode = bool(req.marks) or bool(_EXAM.search(text)) or req.exam_language is not None
    if req.exam_mode and req.task is TaskType.EXPLAIN and req.exam_language is None and not _SIMPLE.search(text):
        req.task = TaskType.ANSWER
    if req.exam_mode and req.mode is AnswerMode.UNDERSTAND and req.exam_language is None:
        req.mode = AnswerMode.EXAM

    # --- detail, format
    req.detail = Detail.BRIEF if _BRIEF.search(text) else Detail.DETAILED if _DETAILED.search(text) else Detail.NORMAL
    if req.mode is AnswerMode.STEPWISE:
        req.output_format = OutputFormat.STEPS
    elif _TABLE.search(text) or req.mode is AnswerMode.COMPARE:
        req.output_format = OutputFormat.TABLE
    elif req.task is TaskType.FLASHCARDS:
        req.output_format = OutputFormat.FLASHCARDS
    elif req.mode in (AnswerMode.EXAM, AnswerMode.REVISION):
        req.output_format = OutputFormat.POINTS
    elif _SPOKEN.search(text) or source is InputSource.VOICE:
        req.output_format = OutputFormat.SPOKEN
    req.diagram = "diagram" in fired
    req.compute = bool(_MATHY.search(text)) or req.task is TaskType.SOLVE

    # --- where the material is
    req.refers_to_screen = bool(_SCREEN.search(text) or _TEACHER_NOW.search(text)) or (
        context is not None and context.kind in ("screen", "selection") and bool(_SCREEN.search(text)))
    req.refers_to_past = bool(_PAST.search(text)) and not req.refers_to_screen
    if req.refers_to_screen and context is not None:
        req.source_required = req.source_required or req.exact_wording

    # --- curriculum
    topical = _LANG_PHRASE.sub(" ", text)    # "in Hindi" names a language, not the subject
    ch, tp = registry.find(topical)
    req.subject = ch.subject if ch else subject_of(topical)
    req.chapter = ch.id if ch else ""
    req.topic = tp.id if tp else ""
    if mastery and req.topic:
        req.known_mastery = {k: v for k, v in mastery.items() if k.endswith(req.topic)}

    # --- privacy
    if source is InputSource.CAPTURE:
        req.privacy = Privacy.SENSITIVE
    elif req.student_answer or req.task in (TaskType.EVALUATE, TaskType.CORRECT) or req.references:
        req.privacy = Privacy.PERSONAL

    # --- what is missing
    if req.task in (TaskType.EVALUATE, TaskType.CORRECT) and not req.student_answer:
        req.missing.append("student_answer")
    if req.source_required and not (req.references or context):
        req.missing.append("source")
    if req.task in (TaskType.EXPLAIN, TaskType.ANSWER, TaskType.DEFINE) and not (req.topic or req.chapter) \
            and len(re.findall(r"\w+", req.question or text)) < 3:
        req.missing.append("topic")

    specific = len(fired) + bool(req.marks) + bool(req.topic) + bool(req.chapter)
    req.confidence = round(min(1.0, 0.35 + 0.15 * specific) - 0.15 * len(req.missing), 2)
    return req


def is_hinglish_request(req: StudyRequest) -> bool:
    return req.response_language is Language.HINGLISH
