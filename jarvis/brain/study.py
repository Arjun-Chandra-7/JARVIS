"""The study assistant: Class 10 CBSE/NCERT-style answers, explanations, revision and quizzes.

Detection is patterns, not a model. What it finds decides the prompt, the output budget and — the
honesty part — the label on the answer:

    "NCERT-style answer"              the style of the board exam; no textbook was consulted
    "Based on the supplied chapter"   chapter text was actually given to the model
    "General Class 10 explanation"    an explanation, not an exam answer

Nothing here claims an answer *comes from* NCERT. An exact quote or page asks for the textbook
itself; without supplied text the answer says it is unavailable instead of inventing one.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

# Class 10 NCERT chapters (rationalised syllabus). Used to name the chapter, never to cite it.
_CHAPTERS = {
    "science": [
        ("Chemical Reactions and Equations", r"chemical reaction|balanc\w+ equation|oxidation|reduction|redox|rancid|corrosion"),
        ("Acids, Bases and Salts", r"\bacids?\b|\bbases?\b|\bph\b|salts?\b|neutrali[sz]ation|indicator|baking soda|bleaching powder"),
        ("Metals and Non-metals", r"ionic compound|ionic bond|\bmetals?\b|non-?metals?|electrovalent|reactivity series|alloy|ores?\b"),
        ("Carbon and its Compounds", r"carbon compound|covalent|hydrocarbon|homologous|ethanol|ethanoic|soap|detergent|catenation"),
        ("Life Processes", r"photosynthesis|respiration|digestion|nutrition|transportation in|excretion|nephron|stomata"),
        ("Control and Coordination", r"neuron|reflex|brain|hormone|phototropism|auxin|nervous system"),
        ("How do Organisms Reproduce?", r"reproduc\w+|pollination|fertili[sz]ation|budding|fission|menstrua"),
        ("Heredity", r"heredity|mendel|dominant|recessive|genes?\b|sex determination"),
        ("Light – Reflection and Refraction", r"reflection|refraction|mirror|lens|focal length|refractive index|magnification"),
        ("The Human Eye and the Colourful World", r"human eye|myopia|hypermetropia|dispersion|prism|scattering|rainbow|tyndall"),
        ("Electricity", r"electric(?:ity|al)?|current|potential difference|voltage|resistance|ohm|resistivity|"
                        r"electron flow|conductor|joule'?s? law|electric power|series|parallel"),
        ("Magnetic Effects of Electric Current", r"magnetic field|solenoid|electromagnet|fleming|right[- ]hand thumb|field lines"),
        ("Our Environment", r"ecosystem|food chain|food web|ozone|biodegradable|trophic"),
    ],
    "maths": [
        ("Real Numbers", r"hcf|lcm|irrational|fundamental theorem of arithmetic|prime factori"),
        ("Polynomials", r"polynomial|zeroes of|quadratic polynomial"),
        ("Pair of Linear Equations in Two Variables", r"linear equations?|pair of equations|substitution method|elimination method"),
        ("Quadratic Equations", r"quadratic equation|discriminant|nature of roots"),
        ("Arithmetic Progressions", r"arithmetic progression|\bap\b|nth term|common difference"),
        ("Triangles", r"similar triangles|\bbpt\b|basic proportionality|thales"),
        ("Coordinate Geometry", r"distance formula|section formula|coordinate geometry"),
        ("Introduction to Trigonometry", r"trigonometr|sin ?[θa]|cos ?[θa]|tan ?[θa]|trigonometric ratio"),
        ("Some Applications of Trigonometry", r"height and distance|angle of elevation|angle of depression"),
        ("Circles", r"tangent to a circle|\bcircles?\b"),
        ("Areas Related to Circles", r"area of (?:a )?sector|segment of a circle|arc length"),
        ("Surface Areas and Volumes", r"surface area|volume of|frustum|hemisphere|cone|cylinder"),
        ("Statistics", r"\bmean\b|median|\bmode\b|statistics|ogive"),
        ("Probability", r"probability"),
    ],
    "social science": [
        ("The Rise of Nationalism in Europe", r"nationalism in europe|unification of (?:germany|italy)|frankfurt parliament"),
        ("Nationalism in India", r"non-?cooperation|civil disobedience|salt march|khilafat|nationalism in india"),
        ("Power Sharing", r"power sharing|belgium|sri lanka"),
        ("Federalism", r"federalism|federal"),
        ("Development", r"\bdevelopment\b|per capita income|human development"),
        ("Sectors of the Indian Economy", r"primary sector|secondary sector|tertiary sector|organised sector"),
        ("Money and Credit", r"money and credit|formal credit|self help group|\bshg\b|barter"),
        ("Resources and Development", r"resources? and development|soil erosion|land use"),
        ("Agriculture", r"agriculture|kharif|rabi|zaid"),
    ],
}
_SUBJECT_WORDS = {
    "physics": "science", "chemistry": "science", "biology": "science", "science": "science",
    "maths": "maths", "math": "maths", "mathematics": "maths", "history": "social science",
    "geography": "social science", "civics": "social science", "economics": "social science",
    "political science": "social science", "sst": "social science", "social science": "social science",
}
_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ek": 1, "do": 2, "teen": 3, "paanch": 5}

_MARKS = re.compile(r"(?i)\b(\d|one|two|three|four|five|ek|do|teen|paanch)[\s-]*marks?(?:er)?\b")
_CLASS = re.compile(r"(?i)\b(?:class|grade|std\.?|standard)\s*(\d{1,2})(?:th)?\b|\b(\d{1,2})(?:th)\s+(?:class|grade|std)\b")
_EXPLICIT = re.compile(
    r"(?i)\b(?:ncert|cbse|board exams?|class \d+|\d+(?:th)? class|marks? answer|\d[\s-]*marks?|"
    r"(?:one|two|three|five)[\s-]marks?|quiz me|test me|exam answer|"
    r"check (?:whether|if) my answer|is my answer (?:correct|right)|syllabus)\b")
# Study words that are also everyday words ("revise my email", "chapter of my life"): they count
# only alongside a syllabus topic or a named subject.
_WEAK = re.compile(r"(?i)\b(?:revis(?:e|ion|ing)|chapter|formula for|numericals?|derivation|my answer is|"
                   r"explain like i'?m revising)\b")
_EXPLAIN = re.compile(r"(?i)\b(?:explain|samjha\w*|samjhao|samjhaiye|teach me|what is meant by|define|difference between)\b")
_QUIZ = re.compile(r"(?i)\b(?:quiz me|test me|ask me (?:a )?questions?|one question at a time|mcqs?)\b")
_CHECK = re.compile(r"(?i)\b(?:check (?:whether|if) my answer|is my answer (?:correct|right)|my answer is|did i get (?:it|this) right)\b")
_REVISE = re.compile(r"(?i)\b(?:revis(?:e|ion|ing)|revising tomorrow|quick recap|last[- ]minute)\b")
_FORMULA = re.compile(r"(?i)\b(?:formula|numerical|calculate|find the (?:value|resistance|current|power)|solve)\b")
_QUOTE = re.compile(r"(?i)\b(?:exact (?:quote|line|lines|words|text)|verbatim|word for word|page (?:no\.? ?)?\d+|"
                    r"as written in (?:the )?(?:ncert|textbook))\b")


@dataclass
class StudyRequest:
    subject: str = ""
    chapter: str = ""
    level: str = "Class 10"
    marks: Optional[int] = None
    mode: str = "explain"            # explain / formal / revise / quiz / check / quote
    style: str = ""                  # exam / steps / quiz / simple / final_only / ""
    formula_first: bool = False
    ncert_named: bool = False
    wants_quote: bool = False
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _topic(text: str) -> tuple[str, str]:
    low = text.lower()
    for subject, chapters in _CHAPTERS.items():
        for name, pattern in chapters:
            if re.search(pattern, low):
                return subject, name
    return "", ""


def detect(text: str) -> Optional[StudyRequest]:
    """A StudyRequest when this is a study request, else None.

    An explicit marker ("NCERT", "3 marks", "quiz me") is enough. Without one, it takes an
    explanation verb *and* a syllabus topic: "why does metal feel colder than wood?" is a question
    to answer conversationally, "explain Ohm's law" is study.
    """
    t = text or ""
    explicit = bool(_EXPLICIT.search(t))
    subject, chapter = _topic(t)
    for word, subj in _SUBJECT_WORDS.items():
        if re.search(rf"(?i)\b{word}\b", t):
            subject = subject or subj
    weak = bool(_WEAK.search(t)) and bool(subject or chapter)
    if not explicit and not weak and not (_EXPLAIN.search(t) and chapter):
        return None
    req = StudyRequest(subject=subject, chapter=chapter)
    m = _CLASS.search(t)
    if m:
        req.level = f"Class {m.group(1) or m.group(2)}"
    m = _MARKS.search(t)
    if m:
        raw = m.group(1).lower()
        req.marks = int(raw) if raw.isdigit() else _NUMBERS.get(raw)
    req.ncert_named = bool(re.search(r"(?i)\bncert|cbse\b", t))
    req.wants_quote = bool(_QUOTE.search(t)) and req.ncert_named
    req.formula_first = bool(_FORMULA.search(t))
    if req.wants_quote:
        req.mode = "quote"
    elif _CHECK.search(t):
        req.mode = "check"
    elif _QUIZ.search(t):
        req.mode, req.style = "quiz", "quiz"
    elif req.marks or re.search(r"(?i)\bexam answer|final exam answer|board answer\b", t):
        req.mode, req.style = "formal", "exam"
    elif _REVISE.search(t):
        req.mode = "revise"
    if re.search(r"(?i)only (?:give )?(?:the )?final (?:exam )?answer|just the answer", t):
        req.style = "final_only"
    elif re.search(r"(?i)step[- ]?by[- ]?step|stepwise|only steps", t):
        req.style = "steps"
    elif re.search(r"(?i)\bsimple|simply|aasan\b", t) and not req.style:
        req.style = "simple"
    return req


# --- answer shape -------------------------------------------------------------------------------
# Words per marks for a CBSE answer, as a band. Checked in tests against model output and used to
# set the output token budget, so a 1-mark answer is not given room for an essay.
LENGTH = {1: (8, 40), 2: (30, 80), 3: (50, 130), 4: (80, 170), 5: (110, 260)}


def length_band(marks: Optional[int]) -> tuple[int, int]:
    return LENGTH.get(marks or 0, (40, 300))


def fits_marks(answer: str, marks: Optional[int]) -> bool:
    body = re.sub(r"^.*?(?:answer|explanation)[^\n]*\n", "", answer or "", count=1, flags=re.I)
    words = len(re.findall(r"\w+", body))
    lo, hi = length_band(marks)
    return lo <= words <= hi


def label(req: StudyRequest, chapter_supplied: bool) -> str:
    """The honest heading for a study answer."""
    marks = f" ({req.marks} mark{'s' if req.marks != 1 else ''})" if req.marks else ""
    if chapter_supplied:
        return f"Based on the supplied chapter{marks}"
    if req.mode == "formal" or req.ncert_named:
        return f"NCERT-style answer{marks}"
    return f"General {req.level} explanation"


_LANG = {
    "en": "Reply in clear, simple English.",
    "hi": "Reply in natural Hindi (Devanagari). Keep scientific terms and formulae in English where Indian classrooms do.",
    "hinglish": ("Reply in natural Hinglish — Hindi in Latin letters, the way a student and a good tutor actually "
                 "talk. Keep technical terms in English (current, electron, potential difference). No stiff "
                 "translation, no Devanagari."),
}


def system_prompt(req: StudyRequest, lang: str, chapter_text: str = "") -> str:
    """The instruction for a study answer. Short: every token here is paid on every study turn."""
    lines = [f"You are a precise {req.level} tutor for CBSE students."]
    if req.subject:
        lines.append(f"Subject: {req.subject}." + (f" Chapter: {req.chapter}." if req.chapter else ""))
    if req.mode == "formal":
        lo, hi = length_band(req.marks)
        lines.append(f"Write a board-exam answer worth {req.marks or 'the stated'} marks: about {lo}-{hi} words, "
                     "one point per mark, exam language, key terms in bold, equations where they earn marks. "
                     "No introduction, no closing remarks.")
    elif req.mode == "revise":
        lines.append("Revision notes for tomorrow's exam: the key points as short bullets, the formulae, one "
                     "common mistake. Nothing else.")
    elif req.mode == "quiz":
        lines.append("Quiz the student ONE question at a time. Ask exactly one exam-style question now and stop. "
                     "Do not give the answer.")
    elif req.mode == "check":
        lines.append("The student gives an answer. Say whether it is correct, what is missing for full marks, "
                     "and the corrected answer in exam form. Be direct and kind.")
    else:
        lines.append("Explain the concept correctly at this level: the idea first, then why, then one everyday "
                     "example. Short paragraphs.")
    if req.formula_first:
        lines.append("Start with the formula, define each symbol with its SI unit, then solve step by step with units.")
    if req.style == "final_only":
        lines.append("Give only the final exam answer — no explanation around it.")
    elif req.style == "steps":
        lines.append("Give numbered steps only.")
    elif req.style == "simple":
        lines.append("Use the simplest words that are still correct.")
    lines.append(_LANG.get(lang, _LANG["en"]))
    if chapter_text:
        lines.append("Use ONLY the supplied chapter text below as the source; if it does not cover the question, "
                     "say so.")
    else:
        lines.append("You have not been given the textbook. Never claim to quote NCERT or give page numbers.")
    return "\n".join(lines)


def quote_unavailable(lang: str) -> str:
    if lang == "hinglish":
        return ("Exact NCERT line ya page number main bina textbook ke nahi de sakta — woh invent ho jayega. "
                "Chapter ka PDF ya page screen par khol do, main wahi se padh ke bataunga.")
    return ("I can't give an exact NCERT quote or page number without the textbook itself — I'd be making it up. "
            "Open the chapter PDF or page on screen and I'll read it from there.")
