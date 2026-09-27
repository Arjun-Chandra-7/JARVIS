"""English, Hindi and Hinglish: detecting what the student speaks, and what they asked for.

Three rules the rest of the package leans on:

* Answer in the language the student used unless they asked otherwise. "Current ka direction …
  kyun hota hai?" is Hinglish and gets Hinglish back — not English, and not Devanagari.
* A request can name two languages for two jobs: "write the exam answer in English but explain it
  in Hinglish". That is two sections, not a compromise.
* Scientific terms students use in English stay in English inside Hindi and Hinglish answers
  ("current", "resistance", "electron"). The glossary says which terms have a Hindi form worth
  giving alongside, and it never swaps in heavy Sanskritised vocabulary unless asked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .types import Language

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN_WORD = re.compile(r"[A-Za-z']+")

# Common Hindi function words and verbs as they are typed in Latin letters. Each is a word a
# purely English sentence essentially never contains; "is", "to", "me" are left out on purpose.
_HINGLISH_WORDS = {
    "kya", "kyu", "kyun", "kyon", "kaise", "kaisa", "kaisi", "kab", "kahan", "kaun", "kitna", "kitni",
    "hai", "hain", "hota", "hoti", "hote", "tha", "thi", "hoga", "hogi", "raha", "rahi", "rahe",
    "ka", "ki", "ke", "ko", "se", "mein", "mai", "par", "aur", "ya", "nahi", "nahin", "bhi",
    "samjha", "samjhao", "samjhaao", "samjhado", "samjhaiye", "samajh", "batao", "bata", "bataiye", "dikhao",
    "karo", "kar", "karna", "karke", "likho", "likh", "do", "dena", "dijiye", "chahiye", "wala", "wali",
    "matlab", "yaani", "accha", "achha", "theek", "thik", "sirf", "bas", "phir", "fir", "abhi",
    "mujhe", "mera", "meri", "mere", "tum", "aap", "hum", "humein", "iska", "iski", "uska", "uski",
    "yeh", "ye", "woh", "wo", "jab", "tab", "agar", "toh", "lekin", "kyunki", "isliye", "wahi", "sahi",
    "galat", "jawab", "sawal", "padhai", "padhna", "yaad", "pehle", "baad", "ulta", "seedha",
}
# Words that are ambiguous with English ("do", "par", "main") only count alongside another marker.
_WEAK = {"do", "par", "bas", "ya", "se", "ko"}

# Explicit language requests. "in simple Hinglish", "hindi mein", "हिंदी में", "in English".
_ASK = {
    Language.HINGLISH: re.compile(r"(?i)\b(?:in\s+(?:simple\s+|easy\s+)?hinglish|hinglish\s+(?:mein|me|main)|hinglish)\b"),
    Language.HINDI: re.compile(r"(?i)(?:\bin\s+(?:simple\s+|pure\s+)?hindi\b|\bhindi\s+(?:mein|me|main)\b|हिंदी\s*में|हिन्दी\s*में|हिंदी|हिन्दी)"),
    Language.ENGLISH: re.compile(r"(?i)\b(?:in\s+(?:simple\s+|plain\s+|formal\s+)?english|english\s+(?:mein|me|main))\b"),
}
# "<exam answer …> in English … explain … in Hinglish" — the language named nearest each job.
_EXAM_JOB = re.compile(r"(?i)(exam|board|formal|written|answer|marks?|likh)")
_EXPLAIN_JOB = re.compile(r"(?i)(explain|samjha|understand|batao|teach|समझा)")


def detect(text: str) -> Language:
    """What the student wrote in. Devanagari → Hindi; enough Latin Hindi words → Hinglish."""
    text = text or ""
    letters = [c for c in text if c.isalpha()]
    if letters:
        dev = sum(1 for c in letters if _DEVANAGARI.match(c))
        if dev / len(letters) >= 0.4:
            return Language.HINDI
    words = [w.lower() for w in _LATIN_WORD.findall(text)]
    if not words:
        return Language.ENGLISH
    strong = sum(1 for w in words if w in _HINGLISH_WORDS and w not in _WEAK)
    weak = sum(1 for w in words if w in _WEAK)
    if strong >= 2 or (strong >= 1 and weak >= 1) or (strong >= 1 and len(words) <= 4):
        return Language.HINGLISH
    return Language.ENGLISH


@dataclass
class LanguagePlan:
    written_in: Language
    respond_in: Language
    exam_in: Optional[Language] = None      # set when the exam answer's language differs
    explicit: bool = False
    signals: list[str] = field(default_factory=list)


def plan(text: str) -> LanguagePlan:
    """Which language(s) to answer in. Explicit requests win over detection."""
    written = detect(text)
    asked = [(m.start(), lang) for lang, rx in _ASK.items() for m in rx.finditer(text or "")]
    # "hinglish" also matches the bare Hindi rule's neighbour words; keep one hit per position.
    asked.sort()
    if not asked:
        return LanguagePlan(written, written, signals=[f"detected:{written}"])
    langs = []
    for _, lang in asked:
        if not langs or langs[-1][1] != lang:
            langs.append((_, lang))
    if len({l for _, l in langs}) >= 2:
        # Two languages named: attach each to the job mentioned closest before it.
        exam_lang = explain_lang = None
        for pos, lang in langs:
            before = (text or "")[max(0, pos - 60):pos]
            last_exam = max((m.end() for m in _EXAM_JOB.finditer(before)), default=-1)
            last_expl = max((m.end() for m in _EXPLAIN_JOB.finditer(before)), default=-1)
            if last_expl > last_exam:
                explain_lang = explain_lang or lang
            elif last_exam >= 0:
                exam_lang = exam_lang or lang
        if exam_lang and explain_lang and exam_lang != explain_lang:
            return LanguagePlan(written, explain_lang, exam_in=exam_lang, explicit=True,
                                signals=[f"split:exam={exam_lang},explain={explain_lang}"])
    lang = langs[-1][1]
    return LanguagePlan(written, lang, explicit=True, signals=[f"asked:{lang}"])


# ------------------------------------------------------------------------------------ glossary
@dataclass(frozen=True)
class Term:
    english: str
    hindi: str                       # the standard Hindi (textbook) term
    hinglish: str                    # how students actually say it — usually the English word
    variants: tuple[str, ...] = ()   # spoken / misheard forms that should map to this term
    pronunciation: str = ""
    ambiguous: str = ""              # other meanings to be careful about
    keep_english: bool = True        # students say the English word even in Hindi sentences
    subject: str = "science"


GLOSSARY: tuple[Term, ...] = (
    Term("electric current", "विद्युत धारा", "current", ("karant", "karent", "dhaara"), "KUR-ent",
         "'current' also means 'present/latest' (current affairs)", subject="physics"),
    Term("conventional current", "परंपरागत धारा", "conventional current", ("conventional karant",),
         subject="physics"),
    Term("electron", "इलेक्ट्रॉन", "electron", ("ilektron",), subject="physics"),
    Term("potential difference", "विभवांतर", "potential difference", ("voltage", "pd", "p.d."),
         ambiguous="'voltage' is used loosely for potential difference", subject="physics"),
    Term("resistance", "प्रतिरोध", "resistance", ("rezistance",), subject="physics"),
    Term("resistivity", "प्रतिरोधकता", "resistivity", (), subject="physics"),
    Term("refraction", "अपवर्तन", "refraction", ("refrection", "apvartan"), subject="physics"),
    Term("reflection", "परावर्तन", "reflection", ("paravartan",), subject="physics"),
    Term("refractive index", "अपवर्तनांक", "refractive index", ("refrective index",), subject="physics"),
    Term("normal", "अभिलंब", "normal", (), ambiguous="'normal' also means 'ordinary'", subject="physics"),
    Term("angle of incidence", "आपतन कोण", "angle of incidence", (), subject="physics"),
    Term("angle of refraction", "अपवर्तन कोण", "angle of refraction", (), subject="physics"),
    Term("ionic compound", "आयनिक यौगिक", "ionic compound", ("ionic compaund",), subject="chemistry"),
    Term("ion", "आयन", "ion", (), subject="chemistry"),
    Term("molten", "गलित", "molten", ("pighla hua",), subject="chemistry"),
    Term("acid", "अम्ल", "acid", ("amla",), subject="chemistry"),
    Term("base", "क्षार", "base", ("kshar",), ambiguous="'base' also means 'bottom' or a number base",
         subject="chemistry"),
    Term("photosynthesis", "प्रकाश संश्लेषण", "photosynthesis", ("foto synthesis",), subject="biology"),
    Term("cumulative frequency", "संचयी बारंबारता", "cumulative frequency", ("cumulative frequncy", "c.f."),
         subject="mathematics"),
    Term("frequency", "बारंबारता", "frequency", (), subject="mathematics"),
    Term("square root", "वर्गमूल", "square root", ("root", "under root"), subject="mathematics"),
    Term("hypotenuse", "कर्ण", "hypotenuse", ("hypotenuse side", "karn"), subject="mathematics"),
    Term("diagonal", "विकर्ण", "diagonal", (), subject="mathematics"),
    Term("arithmetic progression", "समांतर श्रेढ़ी", "AP", ("a.p.",), subject="mathematics"),
)


def term(name: str) -> Optional[Term]:
    n = (name or "").strip().lower()
    for t in GLOSSARY:
        if n == t.english or n == t.hinglish.lower() or n in t.variants or n == t.hindi:
            return t
    return None


def term_in(t: Term, lang: Language, first_use: bool = True) -> str:
    """How to write a term in an answer. Hinglish keeps the English word; Hindi gives the Hindi
    term with the English one in brackets the first time, because that is what exams and teachers
    use and what the student will hear in class."""
    if lang is Language.HINDI:
        return f"{t.hindi} ({t.english})" if first_use else t.hindi
    if lang is Language.HINGLISH:
        return t.hinglish
    return t.english


# Words that betray heavily Sanskritised Hindi, which a student who wrote in Hinglish did not ask
# for. Used by the verifier to catch a model "translating" Hinglish into textbook Hindi.
HEAVY_HINDI = ("अतएव", "तत्पश्चात", "यथोचित", "सम्यक", "किंचित", "उपरोक्त", "अधोलिखित")


def script_matches(text: str, lang: Language) -> bool:
    """Does a produced answer look like the requested language? Cheap, deterministic."""
    got = detect(text)
    if lang is Language.HINDI:
        return got is Language.HINDI
    if lang is Language.HINGLISH:
        return got is Language.HINGLISH and not _DEVANAGARI.search(text)
    return got is Language.ENGLISH or not _DEVANAGARI.search(text)
