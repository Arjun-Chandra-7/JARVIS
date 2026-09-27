"""Study Companion: intent extraction, language behaviour, curriculum, and Cases A and B."""
import pytest

from jarvis.study import intent, languages
from jarvis.study.commands import StudyCompanion
from jarvis.study.curriculum import REGISTRY, Chapter, Registry
from jarvis.study.gateway import FakeStudyGateway
from jarvis.study.types import AnswerMode, Coverage, Language, OutputFormat, Privacy, TaskType


# ------------------------------------------------------------------------------ intent
@pytest.mark.parametrize("text,task,mode,marks", [
    ("Give me a three-mark answer on Ohm's law", TaskType.ANSWER, AnswerMode.EXAM, 3),
    ("Explain Ohm's law, 5 marks", TaskType.ANSWER, AnswerMode.EXAM, 5),
    ("Only final answer: 12 + 30", TaskType.SOLVE, AnswerMode.SHORT, None),
    ("Give steps: solve x^2 - 5x + 6 = 0", TaskType.SOLVE, AnswerMode.STEPWISE, None),
    ("Quiz me from Electricity, one question at a time", TaskType.QUIZ, None, None),
    ("Explain refraction in Hinglish", TaskType.EXPLAIN, AnswerMode.UNDERSTAND, None),
    ("Check my answer", TaskType.EVALUATE, None, None),
    ("Give me a hint for this question", TaskType.HINT, AnswerMode.HINT, None),
    ("Make last-minute revision notes for Electricity", TaskType.REVISE, AnswerMode.REVISION, None),
    ("Difference between series and parallel", TaskType.COMPARE, AnswerMode.COMPARE, None),
    ("Make flashcards for Light", TaskType.FLASHCARDS, AnswerMode.REVISION, None),
    ("I have 20 minutes", TaskType.PLAN, None, None),
    ("Prove that root 2 is irrational", TaskType.PROVE, AnswerMode.STEPWISE, None),
    ("Explain the poem line by line", TaskType.EXPLAIN, AnswerMode.LINE_BY_LINE, None),
])
def test_obvious_phrases_are_rules_not_model_calls(text, task, mode, marks):
    r = intent.extract(text)
    assert r.task is task
    if mode:
        assert r.mode is mode
    assert r.marks == marks


def test_a_doubt_is_not_forced_into_exam_format():
    r = intent.extract("Why is current opposite to electron flow?")
    assert r.task is TaskType.EXPLAIN and r.mode is AnswerMode.UNDERSTAND and not r.exam_mode


@pytest.mark.parametrize("text,marks", [
    ("3 marks", 3), ("three-mark answer", 3), ("2-mark question", 2), ("teen number ka answer", 3),
    ("इसका पाँच अंकों का उत्तर", 5), ("long answer", 5), ("one line answer", 1), ("5 marker", 5),
])
def test_marks_in_three_languages(text, marks):
    assert intent.marks_in(text) == marks


def test_request_carries_curriculum_and_privacy_fields():
    r = intent.extract("Check my answer: my answer is current flows from negative to positive", source=intent.InputSource.TYPED)
    assert r.task is TaskType.EVALUATE and r.student_answer and r.privacy is Privacy.PERSONAL
    r = intent.extract("According to this chapter, what is resistivity?")
    assert r.source_required and "source" in r.missing and r.topic == "resistivity" and r.subject == "physics"
    r = intent.extract("Give the exact textbook answer for Ohm's law")
    assert r.exact_wording and r.source_required
    r = intent.extract("Explain the paragraph currently on my screen")
    assert r.refers_to_screen and not r.refers_to_past
    r = intent.extract("Solve 2x + 3 = 11 step by step")
    assert r.compute and r.output_format is OutputFormat.STEPS
    assert r.request_id.startswith("R") and 0 < r.confidence <= 1


def test_language_phrases_are_not_subjects():
    assert intent.extract("इसका पाँच अंकों का उत्तर हिंदी में दो।").subject == ""
    assert intent.extract("Explain Ohm's law in English").subject == "physics"
    assert intent.extract("Hindi grammar: vakya bhed samjhao").subject == "hindi"


# ------------------------------------------------------------------------------ languages
@pytest.mark.parametrize("text,lang", [
    ("Current ka direction electron flow ke opposite kyun hota hai?", Language.HINGLISH),
    ("इसका पाँच अंकों का उत्तर हिंदी में दो।", Language.HINDI),
    ("Why do ionic compounds conduct electricity when molten?", Language.ENGLISH),
    ("Do the solution for me", Language.ENGLISH),              # "do" alone is English
    ("Ye samajh nahi aaya", Language.HINGLISH),
])
def test_detects_the_students_language(text, lang):
    assert languages.detect(text) is lang


def test_split_language_request():
    p = languages.plan("Write the exam answer in English, but explain it to me in Hinglish.")
    assert p.exam_in is Language.ENGLISH and p.respond_in is Language.HINGLISH


def test_explicit_language_request_wins():
    assert languages.plan("Explain refraction in simple Hinglish").respond_in is Language.HINGLISH
    assert languages.plan("रिफ्रैक्शन समझाओ in English").respond_in is Language.ENGLISH


def test_glossary_keeps_english_terms_students_use():
    t = languages.term("current")
    assert languages.term_in(t, Language.HINGLISH) == "current"
    assert languages.term_in(t, Language.HINDI) == "विद्युत धारा (electric current)"
    assert languages.term_in(t, Language.HINDI, first_use=False) == "विद्युत धारा"
    assert t.ambiguous and t.pronunciation
    assert languages.term("karant") is t          # a spoken variant


def test_script_check_catches_wrong_language():
    assert languages.script_matches("Current ulta hota hai kyunki electrons negative hote hain", Language.HINGLISH)
    assert not languages.script_matches("धारा उलटी होती है", Language.HINGLISH)
    assert languages.script_matches("धारा उलटी दिशा में होती है", Language.HINDI)


# ------------------------------------------------------------------------------ curriculum
def test_curriculum_lookup_prefers_specific_topics():
    ch, tp = REGISTRY.find("why is conventional current opposite to electron flow")
    assert ch.id == "sci.electricity" and tp.id == "current_direction"
    ch, tp = REGISTRY.find("cumulative frequency table")
    assert ch.id == "math.statistics" and tp.id == "cumulative_frequency"


def test_every_initial_subject_is_represented():
    subjects = {c.subject for c in REGISTRY.chapters()}
    assert {"mathematics", "physics", "chemistry", "biology", "history", "geography", "political_science",
            "economics", "english", "hindi"} <= subjects


def test_coverage_is_honest():
    assert REGISTRY.coverage_of("Ohm's law") is Coverage.GENERAL
    assert REGISTRY.coverage_of("quantum chromodynamics of gluons") is Coverage.UNVERIFIED
    desc = REGISTRY.describe()
    assert "Electricity" in desc["physics"]["deep"]
    assert all(not c.numbering_verified for c in REGISTRY.chapters())


def test_imported_chapter_is_marked_as_imported_and_other_boards_work():
    r = Registry()
    ch = r.import_chapter("My School Notes on Magnets", "physics", doc_id="D1")
    assert ch.coverage is Coverage.USER_IMPORTED and ch.source_doc == "D1"
    r.register(Chapter(id="icse.x", subject="physics", title="Sound"), board="ICSE", grade=9)
    assert r.chapters("ICSE", 9)[0].title == "Sound" and r.chapters("CBSE", 10) == [ch]


def test_chapter_numbers_for_revise_chapters_1_to_9():
    maths = REGISTRY.by_number(range(1, 10), "mathematics")
    assert [c.number for c in maths] == list(range(1, 10))


# ------------------------------------------------------------------------------ Case A
def test_case_a_conceptual_hinglish():
    recalled = []
    c = StudyCompanion(FakeStudyGateway(), recall=lambda q: recalled.append(q) or ["an unrelated old note"])
    r = c.ask("Current ka direction electron flow ke opposite kyun hota hai? Simple Hinglish mein samjha.")
    body = " ".join(s.body for s in r.sections)
    assert r.language is Language.HINGLISH and languages.script_matches(body, Language.HINGLISH)
    assert "electron" in body and "+" in body and "−" in body and "ulta" in body
    assert "positive charge" in body                                  # why the convention exists
    assert r.follow_up and "?" in r.follow_up                           # a comprehension check
    assert not recalled and c.recall_calls == 0 and not r.used_memory   # no unrelated memory
    assert r.brain_calls == 0                                           # a verified card answered it
    assert "exam" not in r.label.lower()


# ------------------------------------------------------------------------------ Case B
def test_case_b_three_mark_chemistry_answer():
    c = StudyCompanion(FakeStudyGateway())
    r = c.ask("Give me a three-mark Class 10 answer: Why do ionic compounds conduct electricity when molten but not in solid state?")
    exam = r.section("Exam answer")
    points = [ln for ln in exam.body.splitlines() if ln[:2] in ("1.", "2.", "3.")]
    assert len(points) == 3
    text = exam.body.lower()
    assert "ions" in text and "fixed" in text and "free to move" in text and "molten" in text
    assert "NCERT-style" in r.label and "not quoted from NCERT" in r.label
    assert not any(a.startswith("verify:") for a in r.audit)            # every scoring point present, no misconception
    assert len(exam.body.split()) < 110                                 # no padding
    assert not r.follow_up                                              # no conversational filler


def test_split_language_gives_explanation_and_formal_answer():
    c = StudyCompanion(FakeStudyGateway())
    r = c.ask("Why is current opposite to electron flow? Write the exam answer in English, but explain it to me in Hinglish.")
    assert r.sections[0].language is Language.HINGLISH
    exam = r.section("Exam answer")
    assert exam.language is Language.ENGLISH and "conventional current" in exam.body.lower()


def test_hindi_five_mark_follow_up_uses_session_topic_and_admits_english_only():
    c = StudyCompanion()             # no brain: no Hindi translation available
    c.ask("Start a science study session")
    c.ask("Why do ionic compounds conduct electricity when molten?")
    r = c.ask("इसका पाँच अंकों का उत्तर हिंदी में दो।")
    assert r.section("Exam answer") is not None and "ions" in r.section("Exam answer").body
    assert any("अंग्रेज़ी" in u for u in r.uncertainty)                   # says it is English only
    assert any("marks" in u for u in r.uncertainty)                     # 5 marks needs more than the card has


def test_hindi_exam_answer_via_brain_translation():
    gw = FakeStudyGateway(builder=lambda req: "1. आयनिक यौगिक आयनों से बने होते हैं।\n2. ठोस में आयन जकड़े रहते हैं।\n3. पिघलने पर आयन चल सकते हैं।")
    c = StudyCompanion(gw)
    r = c.ask("आयनिक यौगिक पर तीन अंकों का उत्तर हिंदी में दो — ionic compounds conduct")
    exam = r.section("परीक्षा उत्तर")
    assert exam is not None and exam.language is Language.HINDI and languages.detect(exam.body) is Language.HINDI
    assert gw.calls and gw.calls[0].kind.value == "multilingual_transformation"
