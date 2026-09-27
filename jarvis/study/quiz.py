"""Adaptive quizzes, one question at a time.

1. pick a topic (weakest first) and a difficulty; 2. ask one question — the prompt only, never
the answer; 3. wait; 4. grade; 5. short feedback; 6. update mastery and capture any
misconception the wrong answer reveals; 7. choose the next question from how that went.

Difficulty moves up after two right answers in a row and down after a wrong one. Recently asked
questions are not repeated, and each question has alternative wordings so a repeat later does not
read identically. The answer is revealed before the student responds only when they ask
("show the answer", "I give up") — and that counts as not answered.

The bank below is synthetic practice material written for this project (mostly Electricity, the
tested slice). ``QuizEngine`` accepts other banks, and a gateway-generated question can be added
through ``add`` after it is checked.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from .mastery import MasteryStore
from .science_engine import ScienceError, quantity, unit

_REVEAL = re.compile(r"(?i)\b(?:show|tell|reveal)\s+(?:me\s+)?the\s+answer\b|\bi\s+give\s+up\b|\bi\s+don'?t\s+know\b|\bpata\s+nahi\b|\bskip\b")


@dataclass
class Question:
    id: str
    topic: str                       # "chapter/topic" mastery key
    difficulty: int                  # 1 easy · 2 medium · 3 hard
    qtype: str                       # mcq | vsa | sa | numerical | assertion_reason | formula | case
    prompt: str
    answer: str                      # option letter, keyword spec, or "value unit"
    explanation: str
    options: list[str] = field(default_factory=list)
    keywords: list[list[str]] = field(default_factory=list)
    wrong_means: dict = field(default_factory=dict)   # option letter / "no_unit" → misconception id
    variants: list[str] = field(default_factory=list)
    rel_tol: float = 0.02

    def ask(self, variant: int = 0) -> str:
        text = ([self.prompt] + self.variants)[variant % (1 + len(self.variants))]
        if self.options:
            text += "\n" + "\n".join(f"({chr(97 + i)}) {o}" for i, o in enumerate(self.options))
        return text


E = "sci.electricity"
BANK: list[Question] = [
    Question("e1", f"{E}/current_direction", 1, "mcq", "Conventional current in the external circuit flows from:",
             "b", "Conventional current goes from the positive to the negative terminal outside the cell.",
             ["negative to positive terminal", "positive to negative terminal", "either way", "only inside the cell"],
             wrong_means={"a": "current_equals_electron_flow"},
             variants=["Outside the cell, which way does conventional current go?"]),
    Question("e2", f"{E}/current_direction", 1, "vsa", "What is the SI unit of electric current?", "ampere",
             "The ampere (A): one coulomb per second.", keywords=[["ampere"], ["amp"], [" a "]],
             variants=["Name the SI unit of current."]),
    Question("e3", f"{E}/current_direction", 2, "numerical",
             "A charge of 12 C flows through a wire in 4 s. Find the current.", "3 A",
             "I = Q/t = 12/4 = 3 A.", wrong_means={"no_unit": "omits_units"},
             variants=["12 coulombs pass a point in 4 seconds. What is the current?"]),
    Question("e4", f"{E}/current_direction", 3, "assertion_reason",
             "Assertion (A): Electrons move from the negative terminal to the positive terminal in a circuit.\n"
             "Reason (R): Conventional current is taken in the direction of electron flow.",
             "c", "A is true; R is false — conventional current is opposite to electron flow.",
             ["Both A and R true, R explains A", "Both true, R does not explain A", "A true, R false", "A false, R true"],
             wrong_means={"a": "current_equals_electron_flow", "b": "current_equals_electron_flow"}),
    Question("e5", f"{E}/ohms_law", 1, "formula", "Write Ohm's law as an equation.", "V = IR",
             "V = IR at constant temperature.", keywords=[["v", "ir"], ["v", "i", "r"]],
             variants=["State the equation that links V, I and R."]),
    Question("e6", f"{E}/ohms_law", 2, "numerical", "A 12 V battery drives a current of 0.5 A through a resistor. Find its resistance.",
             "24 ohm", "R = V/I = 12/0.5 = 24 Ω.", wrong_means={"no_unit": "omits_units"},
             variants=["What resistance lets 0.5 A flow from a 12 V supply?"]),
    Question("e7", f"{E}/ohms_law", 3, "sa", "Why must the temperature be constant for Ohm's law to hold?",
             "resistance changes with temperature", "Resistance of a conductor changes with temperature, so V/I would not stay constant.",
             keywords=[["resistance", "temperature"], ["resistance", "change"]]),
    Question("e8", f"{E}/combinations", 1, "mcq", "Two 6 Ω resistors in series have a total resistance of:", "c",
             "In series resistances add: 6 + 6 = 12 Ω.", ["3 Ω", "6 Ω", "12 Ω", "36 Ω"],
             wrong_means={"a": "series_parallel_swap"}),
    Question("e9", f"{E}/combinations", 2, "numerical", "Find the equivalent resistance of 4 Ω and 12 Ω connected in parallel.",
             "3 ohm", "1/R = 1/4 + 1/12 = 4/12, so R = 3 Ω.", wrong_means={"no_unit": "omits_units"},
             variants=["4 Ω and 12 Ω are joined in parallel. What is the combined resistance?"]),
    Question("e10", f"{E}/combinations", 3, "sa", "Why are household appliances connected in parallel, not in series?",
             "same voltage; independent", "Each appliance gets the full supply voltage and can be switched on or off independently.",
             keywords=[["same", "voltage"], ["same", "potential"], ["independent"], ["separately"]]),
    Question("e11", f"{E}/heating_power", 1, "formula", "Write the formula for heat produced in a resistor (Joule's law).",
             "H = I^2 R t", "H = I²Rt.", keywords=[["i²rt"], ["i^2rt"], ["i2rt"], ["i²", "r", "t"]]),
    Question("e12", f"{E}/heating_power", 2, "numerical", "A current of 2 A flows through a 5 Ω resistor for 60 s. Find the heat produced.",
             "1200 J", "H = I²Rt = 4 × 5 × 60 = 1200 J.", wrong_means={"no_unit": "omits_units"}),
    Question("e13", f"{E}/heating_power", 3, "numerical", "An electric heater of 1000 W runs for 2 hours. How much energy does it use in kWh?",
             "2 kWh", "E = P × t = 1 kW × 2 h = 2 kWh.", wrong_means={"no_unit": "omits_units"}),
    Question("e14", f"{E}/resistivity", 2, "mcq", "Resistivity of a wire depends on:", "d",
             "Resistivity is a property of the material (and temperature), not of the wire's length or thickness.",
             ["its length", "its area of cross-section", "the current through it", "the material it is made of"]),
    Question("e15", f"{E}/potential_difference", 1, "vsa", "Name the instrument used to measure potential difference.",
             "voltmeter", "A voltmeter, connected in parallel.", keywords=[["voltmeter"]]),
    Question("e16", f"{E}/potential_difference", 2, "numerical", "How much work is done in moving 2 C of charge across a potential difference of 6 V?",
             "12 J", "W = VQ = 6 × 2 = 12 J.", wrong_means={"no_unit": "omits_units"}),
]


@dataclass
class Attempt:
    qid: str
    topic: str
    correct: bool
    confidence: Optional[float]
    seconds: float
    hints: int
    revealed: bool = False
    misconception: str = ""


@dataclass
class Grade:
    correct: bool
    feedback: str
    misconception: str = ""
    partial: bool = False            # right number, missing unit


class QuizEngine:
    def __init__(self, mastery: MasteryStore, bank: Optional[list[Question]] = None, *,
                 topics: Optional[list[str]] = None, difficulty: int = 1, seed: int = 0,
                 clock=time.time) -> None:
        self.mastery = mastery
        self.bank = list(bank if bank is not None else BANK)
        self.topics = topics or sorted({q.topic for q in self.bank})
        self.difficulty = max(1, min(3, difficulty))
        self.rng = random.Random(seed)
        self.clock = clock
        self.current: Optional[Question] = None
        self.asked_at = 0.0
        self.hints_used = 0
        self.asked: list[str] = []
        self.uses: dict[str, int] = {}
        self.attempts: list[Attempt] = []
        self.run = 0                 # consecutive right answers

    # ------------------------------------------------------------------ asking
    def next(self) -> Optional[str]:
        """The next question's text. Nothing if one is still waiting for an answer."""
        if self.current is not None:
            return None
        q = self._choose()
        if q is None:
            return None
        self.current = q
        self.asked.append(q.id)
        n = self.uses.get(q.id, 0)
        self.uses[q.id] = n + 1
        self.asked_at = self.clock()
        self.hints_used = 0
        return q.ask(variant=n)

    def _choose(self) -> Optional[Question]:
        recent = set(self.asked[-6:])
        weak = self.mastery.weakest([t for t in self.topics], n=len(self.topics))
        pool = [q for q in self.bank if q.topic in self.topics and q.id not in recent]
        if not pool:
            pool = [q for q in self.bank if q.topic in self.topics and q.id != (self.asked[-1] if self.asked else "")]
        if not pool:
            pool = [q for q in self.bank if q.topic in self.topics]     # only one left: repeat, reworded
        if not pool:
            return None
        def rank(q: Question):
            return (abs(q.difficulty - self.difficulty), weak.index(q.topic) if q.topic in weak else 99,
                    self.uses.get(q.id, 0), self.rng.random())
        return min(pool, key=rank)

    def hint(self) -> str:
        if self.current is None:
            return "There's no question waiting."
        self.hints_used += 1
        q = self.current
        if q.qtype == "numerical":
            return "Write the formula that links the quantities first, then substitute with units."
        if q.options:
            return "Rule out the options that contradict the definition."
        return "Think of the key word the definition needs."

    # ------------------------------------------------------------------ answering
    def answer(self, text: str, confidence: Optional[float] = None) -> Grade:
        q = self.current
        if q is None:
            return Grade(False, "There's no question waiting — say “next question”.")
        seconds = round(self.clock() - self.asked_at, 1)
        if _REVEAL.search(text or ""):
            g = Grade(False, f"The answer: {q.answer}. {q.explanation}")
            self._finish(q, g, confidence, seconds, revealed=True)
            return g
        g = self.grade(q, text)
        self._finish(q, g, confidence, seconds)
        return g

    def grade(self, q: Question, text: str) -> Grade:
        t = (text or "").strip().lower()
        if q.options:
            m = re.search(r"\b\(?([a-d])\)?\b", t)
            letter = m.group(1) if m else ""
            if not letter:
                for i, o in enumerate(q.options):
                    if o.lower() in t:
                        letter = chr(97 + i)
            ok = letter == q.answer
            return Grade(ok, ("Right. " if ok else f"Not quite — it's ({q.answer}). ") + q.explanation,
                         "" if ok else q.wrong_means.get(letter, ""))
        if q.qtype == "numerical":
            want = quantity(q.answer)
            m = re.search(r"[-+]?\d+(?:\.\d+)?", t)
            if not m:
                return Grade(False, f"I need a number. {q.explanation}")
            rest = t[m.end():].strip()
            u = rest.split()[0] if rest else ""
            try:
                got = quantity(f"{m.group(0)} {u}".strip()) if u else quantity(m.group(0))
            except ScienceError:
                got = quantity(m.group(0))
            v_want, d_want = want.si()
            if got.unit:
                v_got, d_got = got.si()
                unit_ok = d_got == d_want
            else:
                v_got, unit_ok = got.value * unit(want.unit)[0], False
            ok_value = abs(v_got - v_want) <= q.rel_tol * abs(v_want or 1)
            if ok_value and unit_ok:
                return Grade(True, f"Right — {q.answer}. {q.explanation}")
            if ok_value:
                return Grade(False, f"The number is right, but the unit is missing or wrong: {q.answer}.",
                             q.wrong_means.get("no_unit", "omits_units"), partial=True)
            return Grade(False, f"Not quite — {q.answer}. {q.explanation}")
        ok = any(all(k.strip() in f" {t} " or k in t for k in grp) for grp in q.keywords)
        return Grade(ok, ("Right. " if ok else "Not quite. ") + q.explanation)

    def _finish(self, q: Question, g: Grade, confidence, seconds, revealed=False) -> None:
        event = "incorrect" if not g.correct else ("correct_hint" if self.hints_used else "correct")
        self.mastery.record(q.topic, event, confidence=confidence)
        if g.misconception:
            self.mastery.misconception(g.misconception, q.topic)
        self.attempts.append(Attempt(q.id, q.topic, g.correct, confidence, seconds, self.hints_used, revealed, g.misconception))
        if g.correct and not self.hints_used:
            self.run += 1
            if self.run >= 2:
                self.difficulty, self.run = min(3, self.difficulty + 1), 0
        else:
            self.run = 0
            self.difficulty = max(1, self.difficulty - 1) if not g.correct else self.difficulty
        self.current = None

    def add(self, q: Question) -> None:
        if any(x.id == q.id for x in self.bank):
            raise ValueError("duplicate_question_id")
        self.bank.append(q)

    def summary(self) -> dict:
        n = len(self.attempts)
        right = sum(a.correct for a in self.attempts)
        return {"asked": n, "correct": right, "hints": sum(a.hints for a in self.attempts),
                "misconceptions": sorted({a.misconception for a in self.attempts if a.misconception}),
                "by_topic": {t: {"asked": sum(a.topic == t for a in self.attempts),
                                 "correct": sum(a.topic == t and a.correct for a in self.attempts)}
                             for t in sorted({a.topic for a in self.attempts})},
                "final_difficulty": self.difficulty}
