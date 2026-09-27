"""Study Companion: deterministic maths and science, marks-based rubrics, answer evaluation,
hints, and Cases C (maths correction), D (cumulative frequency) and F (handwritten answer)."""
from fractions import Fraction

import pytest
from study_fixtures import HANDWRITTEN, HANDWRITTEN_MINOR

from jarvis.study import math_engine as m
from jarvis.study import rubrics
from jarvis.study import science_engine as s
from jarvis.study.commands import StudyCompanion
from jarvis.study.gateway import FakeStudyGateway
from jarvis.study.knowledge import CARDS
from jarvis.study.types import AnswerMode


# ------------------------------------------------------------------------------ maths: safety
@pytest.mark.parametrize("bad", [
    '__import__("os").system("ls")', "x.__class__", "exp(1)", "open('f')", "lambda: 1", "9**999", "2^1000",
    "eval(1)", "a" * 200, "import os", "x[0]", "{1}", "1; 2",
])
def test_never_executes_user_code(bad):
    with pytest.raises(m.MathError):
        m.parse(bad)


def test_exact_forms_are_kept():
    assert m.simplify_root(800).pretty() == "20√2"
    assert str(m.evaluate("3/4 + 1/6")) == "11/12 ≈ 0.92"
    assert m.evaluate("√800").pretty() == "20√2" and m.evaluate("√800").decimal(2) == "28.28"
    assert m.evaluate("2(3+4)²").pretty() == "98"
    assert m.evaluate("√12 + √27").pretty() == "5√3"


def test_quadratic_is_solved_the_class_10_way_and_checked():
    sol = m.solve("x^2 - 5x + 6 = 0")
    assert [a.pretty() for a in sol.answers] == ["2", "3"]
    assert any("D = " in st for st in sol.steps) and any("Factorise" in st for st in sol.steps)
    assert all("✓" in v for v in sol.verification)


def test_domain_restrictions_reject_roots_and_flag_typos():
    sol = m.solve("x^2 + x - 12 = 0", positive=True, unit="cm")
    assert [a.pretty() for a in sol.answers] == ["3"] and "-4 (must be positive)" in sol.rejected
    assert sol.final() == "3 cm"
    typo = m.solve("x^2 + 4 = 0", positive=True)
    assert not typo.answers and any("possible_typo" in w for w in typo.warnings)
    with pytest.raises(m.MathError, match="possible_typo"):
        m.probability(7, 5)


def test_irrational_roots_stay_exact():
    sol = m.solve("x^2 - 2 = 0")
    assert {a.pretty() for a in sol.answers} == {"-√2", "√2"}


def test_linear_equation():
    sol = m.solve("2x + 3 = 11")
    assert sol.method == "linear" and sol.answers[0].pretty() == "4"


def test_topic_helpers():
    assert m.ap_nth(Fraction(3), Fraction(4), 10) == 39 and m.ap_sum(Fraction(1), Fraction(1), 100) == 5050
    assert m.distance((0, 0), (3, 4)).pretty() == "5" and m.distance((1, 1), (2, 2)).pretty() == "√2"
    assert m.section_point((1, 2), (4, 8), 1, 2) == (Fraction(2), Fraction(4))
    assert m.probability(2, 6) == Fraction(1, 3)
    assert m.grouped_mean([5, 15, 25], [2, 3, 5]).pretty() == "18"
    assert m.TRIG_EXACT[60][0] == "√3/2" and m.TRIG_EXACT[90][2] == "not defined"
    assert m.convert(1, "m", "cm", power=2) == pytest.approx(10000)
    with pytest.raises(m.MathError):
        m.convert(1, "m", "s")
    assert m.pythagoras_check(3, 4, 5) and not m.pythagoras_check(3, 4, 4)


# ------------------------------------------------------------------------------ Case C
def test_case_c_step_checker_finds_the_missing_square_root():
    chk = m.check_steps(["d² = 20² + 20²", "d² = 800", "d = 800"])
    assert not chk.ok and chk.wrong_line == 3 and chk.misconception == "forgot_square_root"
    assert "√800" in chk.explanation and "20√2" in chk.explanation


def test_case_c_through_the_companion():
    c = StudyCompanion(FakeStudyGateway())
    r = c.ask("Check my solution. A student calculates a diagonal using d^2=800 and then writes d=800.")
    txt = r.text()
    assert "line 2" in txt.lower() or "Line 2" in txt
    assert "√800" in txt and "20√2" in txt and "(20√2)² = 800 ✓" in txt
    assert "cannot grade reliably without a marking scheme" in txt          # no false precision
    assert c.mastery.misconceptions["forgot_square_root"].count == 1
    assert r.brain_calls == 0


def test_correct_working_is_confirmed():
    c = StudyCompanion()
    r = c.ask("Check my answer", student_answer="x^2 = 49\nx = 7")
    assert "follows" in r.text()


# ------------------------------------------------------------------------------ science
def test_ohms_law_with_units_substitution_and_verification():
    calc = s.calculate("V=IR", "R", {"V": "12 V", "I": "0.5 A"})
    assert calc.display == "24 Ω" and calc.substitution == "12/0.5 = 24"
    assert "both sides equal ✓" in calc.verification[0]
    steps = calc.steps()
    assert steps[0].startswith("Given") and steps[2].startswith("Formula") and "Answer: R = 24 Ω" in steps


def test_units_are_converted_and_checked():
    assert s.calculate("P=1/f", "P", {"f": "50 cm"}).display == "2 D"
    assert s.calculate("H=I^2*R*t", "H", {"I": "2 A", "R": "5 ohm", "t": "1 min"}).display == "1200 J"
    calc = s.calculate("R=rho*l/A", "R", {"rho": "1.6e-8 ohm m", "l": "2 m", "A": "0.5 mm²"})
    assert calc.display == "0.064 Ω" and calc.conversions
    with pytest.raises(s.ScienceError, match="unit_mismatch"):
        s.calculate("V=IR", "R", {"V": "12 V", "I": "0.5 V"})
    assert "missing_unit:V" in s.calculate("V=IR", "R", {"V": "12", "I": "0.5 A"}).warnings


def test_parallel_combination_and_significant_figures():
    assert s.calculate("1/Rp=1/R1+1/R2", "Rp", {"R1": "4 ohm", "R2": "12 ohm"}).display == "3 Ω"
    assert s.sig(0.0333333, 3) == "0.0333" and s.sig(1234.5, 2) == "1200"


def test_student_numeric_answer_units():
    calc = s.calculate("V=IR", "R", {"V": "12 V", "I": "0.5 A"})
    assert s.check_numeric_answer("24", calc) == {"value_ok": True, "unit_given": False, "unit_ok": False}
    assert s.check_numeric_answer("24 Ω", calc)["unit_ok"]


def test_companion_solves_science_numericals_stepwise_and_short():
    c = StudyCompanion()
    r = c.ask("A 12 V battery is connected to a resistor and a current of 0.5 A flows. Find the resistance, step by step.")
    assert r.mode is AnswerMode.STEPWISE and "R = 24 Ω" in r.text() and "Formula: V = I*R" in r.text()
    r = c.ask("Only final answer: find the equivalent resistance of 4 Ω and 12 Ω in parallel")
    assert r.sections[0].body.strip() == "Rp = 3 Ω"


def test_companion_solves_maths():
    c = StudyCompanion()
    r = c.ask("Solve step by step: x^2 - 5x + 6 = 0")
    assert "x = 2, 3" in r.text() and "Check" in r.text()
    r = c.ask("Find the length of the side if x^2 + x - 12 = 0")
    assert "x = 3" in r.text() and "-4 (must be positive)" in r.text()


# ------------------------------------------------------------------------------ rubrics
def test_mark_scheme_from_card_matches_marks():
    sch = rubrics.scheme_from_card(CARDS["ionic_properties"], 3)
    assert sch.total == 3 and len(sch.points) == 3 and not sch.official
    assert sum(p.marks for p in sch.points) == 3


def test_wording_differences_are_not_penalised():
    sch = rubrics.scheme_from_card(CARDS["ionic_properties"], 3)
    answer = ("They consist of charged ions.\nIn a solid, the ions are fixed in place so they cannot move.\n"
              "Once melted the ions become free to move, carrying charge.")
    ev = rubrics.evaluate(answer, sch, misconception_ids=CARDS["ionic_properties"].misconceptions)
    assert not ev.missing and not ev.inaccurate
    assert ev.marks_estimate.startswith("likely 3/3") and "not an official marking scheme" in ev.marks_estimate


def test_evaluation_identifies_the_inaccurate_line_and_missing_point():
    card = CARDS["ionic_properties"]
    answer = "Ionic compounds are made of ions.\nWhen molten, free electrons carry the current."
    ev = rubrics.evaluate(answer, rubrics.scheme_from_card(card, 3), misconception_ids=card.misconceptions, card=card)
    assert ev.wrong_line == 2 and ev.inaccurate and "ions move" in ev.inaccurate[0].lower() or "ions" in ev.inaccurate[0]
    assert any("solid" in x.lower() for x in ev.missing)
    assert ev.corrected.startswith("1.") and ev.next_step
    assert "/" in ev.marks_estimate and "." not in ev.marks_estimate.split("/")[0][-2:]   # no 4.37-style precision


def test_no_scheme_means_no_marks():
    ev = rubrics.evaluate("Some answer about a topic.", None)
    assert ev.marks_estimate == "cannot grade reliably without a marking scheme" and not ev.graded


def test_official_scheme_changes_the_wording():
    sch = rubrics.scheme_from_card(CARDS["ohms_law"], 3)
    sch.official, sch.source = True, "Sample marking scheme p. 4"
    ev = rubrics.evaluate("V = IR at constant temperature, V is proportional to current, graph is a straight line", sch)
    assert "official" not in ev.marks_estimate and ev.marks_estimate.startswith("likely")


# ------------------------------------------------------------------------------ Case F
def test_case_f_handwriting_uncertain_words_are_not_penalised():
    card = CARDS["ionic_properties"]
    ev = rubrics.evaluate(HANDWRITTEN.text(), rubrics.scheme_from_card(card, 3), misconception_ids=card.misconceptions,
                          uncertain_words=[w.text for w in HANDWRITTEN.uncertain], card=card)
    assert not ev.missing and not ev.inaccurate
    assert ev.marks_estimate.startswith("approximately 2–3 marks")                   # a band, not a guess
    assert ev.clarify and "iens" in ev.clarify


def test_case_f_uncertainty_that_does_not_matter_is_shown_but_not_asked_about():
    card = CARDS["ionic_properties"]
    ev = rubrics.evaluate(HANDWRITTEN_MINOR.text(), rubrics.scheme_from_card(card, 3), misconception_ids=card.misconceptions,
                          uncertain_words=[w.text for w in HANDWRITTEN_MINOR.uncertain], card=card)
    assert ev.marks_estimate.startswith("likely 3/3") and not ev.clarify and ev.uncertain_words


def test_case_f_through_the_companion():
    c = StudyCompanion(FakeStudyGateway())
    r = c.ask("Check my answer: why do ionic compounds conduct electricity when molten?", ocr=HANDWRITTEN)
    txt = r.text()
    assert "Couldn't read clearly" in txt and "“iens”" in txt and "not counted against you" in txt
    assert "Missing" not in [s.heading for s in r.sections]
    assert r.follow_up and "confirm" in r.follow_up                      # asks only because it matters
    assert r.brain_calls == 0


def test_clear_handwriting_does_not_trigger_a_clarifying_question():
    c = StudyCompanion()
    r = c.ask("Check my answer on ionic compounds", student_answer=(
        "Ionic compounds are made of ions. In the solid state the ions are fixed and cannot move. "
        "When molten the ions are free to move and carry charge."))
    assert not r.follow_up and "likely 3/3" in r.text()


# ------------------------------------------------------------------------------ Case D
def test_case_d_cumulative_frequency():
    c = StudyCompanion()
    r = c.ask("How do I know this table is cumulative frequency?")
    txt = r.text()
    assert "never" in txt.lower() or "only go up" in txt
    assert r.section("Table").kind == "table" and "12 − 5 = 7" in r.section("Table").body
    assert "Check: 5 + 7 + 8 + 6 + 4 = 30 = last c.f. 30 ✓" in r.section("Verify by subtraction").body
    assert r.follow_up


def test_case_d_uses_the_students_own_table():
    c = StudyCompanion()
    r = c.ask("Is 4, 9, 15, 22 a cumulative frequency column? explain")
    assert "22 − 15 = 7" in r.text()
    assert m.is_cumulative([4, 9, 15, 22]) and not m.is_cumulative([4, 9, 7])
    with pytest.raises(m.MathError):
        m.frequencies_from_cumulative([4, 9, 7])


# ------------------------------------------------------------------------------ hints
def test_hints_escalate_only_when_asked_and_never_reveal_by_default():
    c = StudyCompanion()
    h1 = c.ask("Give me a hint: solve x^2 - 5x + 6 = 0")
    assert "ax² + bx + c" in h1.text() and "2, 3" not in h1.text()
    h2 = c.ask("another hint")
    assert "discriminant" in h2.text() and "2, 3" not in h2.text()
    h3 = c.ask("next hint")
    assert "2, 3" not in h3.text()
    h4 = c.ask("hint")
    assert "2, 3" not in h4.text() and "show the answer" in h4.text()
    full = c.ask("hint — show the answer")
    assert "x = 2, 3" in full.text()


def test_hints_only_session_mode_withholds_solutions():
    c = StudyCompanion()
    c.ask("Only give hints")
    r = c.ask("Solve 2x + 3 = 11")
    assert "Hint" in r.text() and "x = 4" not in r.text()
