"""Answer profiles: the same knowledge, shaped for what the student asked for.

* **Understand** — intuition first, an everyday example, the precise statement afterwards, one
  short question to check understanding, a diagram when it helps.
* **Exam** — direct, marks-appropriate: one numbered point per scoring idea, formula/diagram when
  the scheme needs them, no conversational filler.
* **Revision** — essentials, formulae, common mistakes, likely questions, quick recall checks,
  with a hard cap on length.
* **Stepwise** — given, required, formula, substitution, calculation, units, final answer,
  verification.
* **Hint** — the smallest useful next step; the next hint only when asked; never the answer
  unless the student says so.
* **Line-by-line** — a short excerpt of each line, its meaning, the key term, the link to the
  bigger idea.
* **Compare** — named dimensions, a table, similarities, differences, a one-line conclusion.

Headings follow the response language. Everything here is formatting over content that has
already been produced by a card, an engine or the brain — no facts originate in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from . import languages
from .knowledge import Card
from .math_engine import Solution
from .science_engine import Calculation
from .types import Language, Section

EN, HG, HI = Language.ENGLISH, Language.HINGLISH, Language.HINDI

HEADINGS = {
    "understand": {EN: "Understand it", HG: "Samjho", HI: "समझिए"},
    "example": {EN: "Everyday example", HG: "Roz ki life se example", HI: "रोज़मर्रा का उदाहरण"},
    "precise": {EN: "In exact terms", HG: "Exact definition", HI: "सटीक रूप में"},
    "check": {EN: "Check yourself", HG: "Ek chhota sawaal", HI: "ख़ुद जाँचिए"},
    "exam": {EN: "Exam answer", HG: "Exam answer", HI: "परीक्षा उत्तर"},
    "steps": {EN: "Solution", HG: "Solution", HI: "हल"},
    "hint": {EN: "Hint", HG: "Hint", HI: "संकेत"},
    "revision": {EN: "Last-minute revision", HG: "Last-minute revision", HI: "अंतिम समय की दोहराई"},
    "formulae": {EN: "Formulae", HG: "Formulae", HI: "सूत्र"},
    "mistakes": {EN: "Common mistakes", HG: "Common galtiyan", HI: "आम गलतियाँ"},
    "likely": {EN: "Likely questions", HG: "Aane wale sawaal", HI: "संभावित प्रश्न"},
    "recall": {EN: "Quick recall", HG: "Jaldi yaad karo", HI: "झटपट याद"},
    "chain": {EN: "Cause → effect", HG: "Kyun → kya hota hai", HI: "कारण → परिणाम"},
}


def h(key: str, lang: Language) -> str:
    return HEADINGS[key].get(lang, HEADINGS[key][EN])


def understand(card: Card, lang: Language, *, include_definition: bool = True) -> tuple[list[Section], str]:
    """Sections plus the comprehension-check question."""
    paras = card.explain.get(lang) or card.explain[EN]
    out = [Section(h("understand", lang), "\n\n".join(paras), lang)]
    if card.example.get(lang):
        out.append(Section(h("example", lang), card.example[lang], lang))
    if include_definition and card.definition.get(lang):
        out.append(Section(h("precise", lang), card.definition[lang], lang))
    chk = card.check.get(lang) or card.check[EN]
    return out, f"{h('check', lang)}: {chk.question}"


def exam(sentences: Sequence[str], marks: Optional[int], lang: Language, *, formula: str = "",
         diagram_note: str = "") -> Section:
    title = h("exam", lang) + (f" ({marks} mark{'s' if marks != 1 else ''})" if marks else "")
    lines = [f"{i}. {s}" for i, s in enumerate(sentences, start=1)]
    if formula:
        lines.append(f"Formula: {formula}")
    if diagram_note:
        lines.append(diagram_note)
    return Section(title, "\n".join(lines), lang, kind="points")


def revision(cards: Sequence[Card], lang: Language, max_lines: int = 18) -> list[Section]:
    essentials, formulae, mistakes, likely, recall = [], [], [], [], []
    for c in cards:
        essentials += c.revision
        formulae += [f for f in c.formulae if f not in formulae]
        mistakes += c.mistakes[:2]
        likely += c.likely[:1]
        chk = c.check.get(lang) or c.check[EN]
        recall.append(f"{chk.question} → {chk.answer}")
    budget = max_lines
    out = []
    for key, items in (("revision", essentials), ("formulae", formulae), ("mistakes", mistakes),
                       ("likely", likely), ("recall", recall)):
        take = items[: max(1, min(len(items), budget // 3 if key == "revision" else 3))]
        if not take or budget <= 0:
            continue
        take = take[:budget]
        budget -= len(take)
        out.append(Section(h(key, lang), "\n".join(f"• {x}" for x in take), lang, kind="points"))
    return out


def stepwise_math(sol: Solution, lang: Language) -> Section:
    lines = [f"Given: {'; '.join(sol.given)}", f"Required: {sol.required}"]
    if sol.formula:
        lines.append(f"Formula/method: {sol.formula}")
    lines += sol.steps
    if sol.rejected:
        lines.append("Rejected: " + "; ".join(sol.rejected))
    lines.append(f"Final answer: {sol.required} = {sol.final()}")
    lines += [f"Check: {v}" for v in sol.verification]
    return Section(h("steps", lang), "\n".join(lines), lang, kind="steps")


def stepwise_science(calc: Calculation, lang: Language) -> Section:
    lines = calc.steps() + [f"Check: {v}" for v in calc.verification]
    if calc.formula.note:
        lines.append(f"Note: {calc.formula.note}")
    return Section(h("steps", lang), "\n".join(lines), lang, kind="steps")


def short(final: str, lang: Language) -> Section:
    return Section("", final, lang)


# ------------------------------------------------------------------------------------ hints
@dataclass
class HintLadder:
    hints: list[str]
    answer: str

    def get(self, level: int, reveal: bool = False) -> tuple[str, bool]:
        """(text, is_answer). Level starts at 1; past the last hint only ``reveal`` gives the answer."""
        if reveal:
            return self.answer, True
        if level <= len(self.hints):
            return self.hints[level - 1], False
        return "That's every hint I have — say “show the answer” when you want the full solution.", False


def hints_for_solution(sol: Solution) -> HintLadder:
    hints = []
    if sol.method == "quadratic":
        hints = ["Write it in the form ax² + bx + c = 0 and note a, b and c.",
                 "Work out the discriminant D = b² − 4ac — it tells you what kind of roots to expect.",
                 "Try to split the middle term (or use the quadratic formula) and set each factor to zero."]
    elif sol.method == "linear":
        hints = ["Collect the terms with the variable on one side.", "Divide by the coefficient of the variable."]
    elif sol.method == "square_root":
        hints = [f"You have {sol.required}², not {sol.required}. What undoes squaring?",
                 "Take the square root — and simplify the surd by pulling out a perfect square."]
    return HintLadder(hints or ["Write down what is given and what is asked."], f"{sol.required} = {sol.final()}")


def hints_for_calculation(calc: Calculation) -> HintLadder:
    f = calc.formula
    return HintLadder([f"Which quantity is asked for, and which formula links it to what you know? ({f.name})",
                       f"Use {f.equation}. Are all your values in SI units?",
                       f"Rearrange for {calc.target} and substitute."], f"{calc.target} = {calc.display}")


def hints_for_card(card: Card, lang: Language) -> HintLadder:
    ideas = [p.idea for p in card.points if p.required]
    first = {EN: "Think about what has to move for electricity to flow — or what the key idea of this topic is.",
             HG: "Socho — is topic ka sabse zaroori idea kya hai?", HI: "सोचो — इस विषय का सबसे ज़रूरी विचार क्या है?"}
    hints = [first.get(lang, first[EN])] + [f"Key idea: {i.split(',')[0]}." for i in ideas[:2]]
    return HintLadder(hints, "\n".join(card.exam_answer(3)))


# ------------------------------------------------------------------------------------ line by line / compare
def line_by_line(lines: Sequence[str], meanings: Sequence[str], lang: Language, big_idea: str = "") -> Section:
    out = []
    for i, (ln, meaning) in enumerate(zip(lines, meanings), start=1):
        excerpt = ln if len(ln) <= 80 else ln[:77].rsplit(" ", 1)[0] + "…"
        terms = [t.english for t in languages.GLOSSARY if t.english in ln.lower()]
        row = [f"{i}. “{excerpt}”", f"   Meaning: {meaning}"]
        if terms:
            row.append(f"   Key term: {terms[0]}")
        out.append("\n".join(row))
    if big_idea:
        out.append(f"Connection: {big_idea}")
    return Section("Line by line", "\n".join(out), lang, kind="steps")


def compare_table(a: str, b: str, rows: Sequence[tuple[str, str, str]], similar: Sequence[str], conclusion: str,
                  lang: Language) -> list[Section]:
    table = [f"| Basis | {a} | {b} |", "|---|---|---|"] + [f"| {d} | {x} | {y} |" for d, x, y in rows]
    out = [Section("Differences", "\n".join(table), lang, kind="table")]
    if similar:
        out.append(Section("Similarities", "\n".join(f"• {s}" for s in similar), lang, kind="points"))
    out.append(Section("In one line", conclusion, lang))
    return out
