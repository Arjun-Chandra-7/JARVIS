"""Study Companion: adaptive quiz (Case G), mastery and misconceptions, revision planning
(Case H), spaced repetition, and study sessions."""
import datetime as dt
import json
import time

import pytest

from jarvis.study import revision
from jarvis.study.commands import StudyCompanion
from jarvis.study.curriculum import REGISTRY
from jarvis.study.mastery import DISCLAIMER, MasteryStore, TopicState
from jarvis.study.quiz import BANK, QuizEngine
from jarvis.study.session import SessionManager


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------------------ quiz engine
def _answer_for(q, right: bool, *, wrong_mcq: str = ""):
    if q.options:
        return q.answer if right else (wrong_mcq or next(ch for ch in "abcd" if ch != q.answer))
    if q.qtype == "numerical":
        return q.answer if right else "999"
    return q.keywords[0][0] if right else "no idea really"


def test_case_g_ten_questions_one_at_a_time_with_adaptation(tmp_path):
    clock = Clock()
    ms = MasteryStore(tmp_path / "mastery.json")
    qz = QuizEngine(ms, clock=clock, difficulty=1)
    script = [True, True, True, True, False, "wrong_a", True, "no_unit", True, True]
    seen_texts, difficulties = [], []
    for i, outcome in enumerate(script):
        text = qz.next()
        assert text and qz.next() is None                      # one at a time: nothing new while one waits
        q = qz.current
        assert q.answer not in text.split("\n")[0] or q.options  # the prompt never carries the answer
        assert q.explanation not in text
        seen_texts.append(text)
        clock.t += 20 + i
        if outcome == "wrong_a":
            q_mcq = q
            ans = "a" if q.options else "999"
        elif outcome == "no_unit":
            ans = q.answer.split()[0] if q.qtype == "numerical" else _answer_for(q, False)
        else:
            ans = _answer_for(q, outcome)
        g = qz.answer(ans, confidence=0.6)
        assert g.feedback and len(g.feedback) < 220            # concise
        difficulties.append(qz.difficulty)
    assert len(qz.attempts) == 10 and len(set(a.qid for a in qz.attempts)) >= 8   # little repetition
    assert max(difficulties) >= 2                               # moved up after a run of right answers
    wrong_idx = [i for i, a in enumerate(qz.attempts) if not a.correct]
    assert wrong_idx and any(difficulties[i] <= difficulties[i - 1] for i in wrong_idx if i)   # eased after a miss
    assert all(a.seconds > 0 for a in qz.attempts)
    assert ms.topics and any(s.practised for s in ms.topics.values())
    summary = qz.summary()
    assert summary["asked"] == 10 and 0 < summary["correct"] < 10


def test_quiz_captures_the_misconception_behind_a_wrong_option(tmp_path):
    ms = MasteryStore(tmp_path / "m.json")
    e1 = next(q for q in BANK if q.id == "e1")
    qz = QuizEngine(ms, bank=[e1])
    qz.next()
    g = qz.answer("a")
    assert not g.correct and g.misconception == "current_equals_electron_flow"
    rec = ms.misconceptions["current_equals_electron_flow"]
    assert rec.count == 1 and rec.topic == "sci.electricity/current_direction"


def test_numerical_without_unit_is_partial_and_records_omitted_units(tmp_path):
    ms = MasteryStore()
    e6 = next(q for q in BANK if q.id == "e6")
    qz = QuizEngine(ms, bank=[e6])
    qz.next()
    g = qz.answer("24")
    assert not g.correct and g.partial and "unit" in g.feedback and g.misconception == "omits_units"
    qz.next()
    assert qz.answer("24 ohm").correct


def test_answer_is_not_revealed_unless_asked(tmp_path):
    ms = MasteryStore()
    qz = QuizEngine(ms, bank=[next(q for q in BANK if q.id == "e3")])
    text = qz.next()
    assert "3 A" not in text and "12/4" not in text
    hint = qz.hint()
    assert "3 A" not in hint
    g = qz.answer("show me the answer")
    assert "3 A" in g.feedback and not g.correct and qz.attempts[-1].revealed
    assert ms.topics["sci.electricity/current_direction"].incorrect == 1


def test_hint_use_counts_as_correct_with_hint(tmp_path):
    ms = MasteryStore()
    qz = QuizEngine(ms, bank=[next(q for q in BANK if q.id == "e5")])
    qz.next()
    qz.hint()
    assert qz.answer("V = IR").correct
    st = ms.topics["sci.electricity/ohms_law"]
    assert st.correct_with_hint == 1 and st.correct_independent == 0


def test_repeat_questions_use_alternative_wording():
    ms = MasteryStore()
    q = next(q for q in BANK if q.id == "e1")
    qz = QuizEngine(ms, bank=[q, next(x for x in BANK if x.id == "e2")])
    first = []
    for _ in range(4):
        t = qz.next()
        first.append(t)
        qz.answer("b")
    e1_texts = [t for t in first if "terminal" in t or "Outside" in t]
    assert len(set(t.split("\n")[0] for t in e1_texts)) == len(e1_texts)


def test_quiz_through_the_companion_one_question_per_turn():
    c = StudyCompanion()
    r = c.ask("Quiz me from Electricity, one question at a time.")
    assert r.section("Question 1") and r.follow_up
    q = c.quiz.current
    r2 = c.ask(_answer_for(q, True))
    assert r2.sections[0].heading == "✓" and r2.section("Question 2")
    assert len([s for s in r2.sections if s.heading.startswith("Question")]) == 1
    r3 = c.ask("stop the quiz")
    assert "correct" in r3.text() and c.quiz is None


# ------------------------------------------------------------------------------ mastery
def test_mastery_updates_and_estimate_is_modest_and_labelled():
    ms = MasteryStore()
    k = "sci.electricity/ohms_law"
    ms.record(k, "explained")
    for ev in ("correct", "correct", "correct_hint", "incorrect"):
        ms.record(k, ev, confidence=0.7)
    s = ms.topics[k]
    assert (s.seen, s.explained, s.practised, s.correct_independent, s.correct_with_hint, s.incorrect) == (1, 1, 4, 2, 1, 1)
    assert s.estimate == round((2.5 + 1) / (4 + 2), 3) and s.evidence == 4 and s.confidence == 0.7
    assert ms.view()["note"] == DISCLAIMER


def test_misconceptions_are_stored_separately_and_repeats_are_counted():
    ms = MasteryStore()
    k = "math.triangles/pythagoras"
    ms.record(k, "incorrect")
    ms.misconception("forgot_square_root", k)
    ms.misconception("forgot_square_root", k)
    assert ms.misconceptions["forgot_square_root"].count == 2 and ms.topics[k].repeated_mistakes == 1
    assert ms.misconception("not_a_known_one", k) is None
    ms.resolve("forgot_square_root")
    assert not ms.active_misconceptions()


def test_no_traits_are_ever_inferred():
    ms = MasteryStore()
    ms.record("a/b", "incorrect")
    ms.misconception("omits_units", "a/b")
    blob = json.dumps(ms.view()).lower()
    for word in ("intelligen", "iq", "ability", "personality", "lazy", "slow learner", "gifted"):
        assert word not in blob


def test_student_controls_view_correct_reset_delete_disable(tmp_path):
    path = tmp_path / "mastery.json"
    ms = MasteryStore(path)
    ms.record("sci.electricity/ohms_law", "incorrect")
    ms.misconception("omits_units", "sci.electricity/ohms_law")
    assert oct(path.stat().st_mode)[-3:] == "600"
    ms.correct("sci.electricity/ohms_law", 0.9)
    assert MasteryStore(path).estimate("sci.electricity/ohms_law") == 0.9          # persisted
    assert ms.reset_topic("sci.electricity/ohms_law") and "omits_units" not in ms.misconceptions
    ms.record("x/y", "correct")
    ms.set_personalization(False)
    ms.record("x/y", "incorrect")
    assert ms.topics["x/y"].incorrect == 0 and ms.estimate("x/y") is None and ms.weakest(["p", "q"]) == ["p", "q"]
    ms.delete_all()
    assert not path.exists() and not ms.topics


def test_companion_privacy_commands(tmp_path):
    c = StudyCompanion(data_dir=tmp_path)
    c.ask("Check my answer", student_answer="d^2 = 800\nd = 800")
    assert "forgot" in c.ask("Show my progress").text().lower() or c.mastery.topics
    assert "Forgets the square root" in c.ask("What did I get wrong?").text()
    c.ask("Reset my progress in Pythagoras")
    assert not c.mastery.topics
    c.ask("Turn off personalization")
    c.ask("Check my answer", student_answer="d^2 = 800\nd = 800")
    assert not c.mastery.topics and not c.mastery.misconceptions
    c.ask("Turn on personalization")
    c.ask("Check my answer", student_answer="d^2 = 800\nd = 800")
    assert c.mastery.misconceptions
    r = c.ask("Delete my study history")
    assert "Deleted" in r.text() and not (tmp_path / "mastery.json").exists() and not (tmp_path / "session.json").exists()


def test_answer_text_is_not_stored_in_history(tmp_path):
    c = StudyCompanion(data_dir=tmp_path)
    c.ask("Start a science study session")
    c.ask("Check my answer", student_answer="My secret phrase zebra42: d^2 = 800\nd = 800")
    for f in tmp_path.iterdir():
        assert "zebra42" not in f.read_text()


# ------------------------------------------------------------------------------ revision
def test_case_h_twenty_five_minutes_test_tomorrow(tmp_path):
    ms = MasteryStore()
    for _ in range(3):
        ms.record("sci.electricity/combinations", "incorrect")
    ms.misconception("series_parallel_swap", "sci.electricity/combinations")
    ms.record("sci.electricity/current_direction", "correct")
    ms.record("sci.electricity/current_direction", "correct")
    p = revision.plan(25, chapters=[REGISTRY.chapter("sci.electricity")], mastery=ms, exam_in_days=1)
    assert p.total() <= 25 and p.total() >= 20
    acts = [b.activity for b in p.blocks]
    assert "recall" in acts and "practice" in acts and acts[-1] == "formula_run"
    assert p.blocks[0].activity == "mistakes"                           # repeated mistake first
    assert p.focus[0] == "sci.electricity/combinations"                 # weakest topic first
    assert "sci.electricity/current_direction" not in p.focus[:1]
    assert all(5 >= b.minutes or b.activity in ("recall", "practice") for b in p.blocks)
    assert not p.creates_calendar_events and p.reminder_request["needs_approval"]
    assert any("tomorrow" in n.lower() for n in p.notes)


def test_case_h_through_the_companion_makes_no_calendar_action():
    c = StudyCompanion()
    r = c.ask("I have 25 minutes and my Electricity test is tomorrow.")
    assert "plan:no_calendar_action" in r.audit and "calendar" in r.follow_up
    assert "25 minutes" in r.sections[0].heading or r.sections[0].heading.startswith("2")


@pytest.mark.parametrize("text,check", [
    ("I have 20 minutes", lambda p: p.total() <= 20),
    ("Give me a last-second formula run for Electricity", lambda p: p.blocks[0].activity == "formula_run"),
    ("Only revise my weak topics in Electricity, I have 30 minutes", lambda p: p.total() <= 30),
])
def test_revision_requests(text, check):
    c = StudyCompanion()
    r = c.ask(text)
    assert r.label == "Revision plan"


def test_revise_chapters_range_uses_numbers_and_says_they_are_unverified():
    c = StudyCompanion()
    r = c.ask("Plan revision: revise chapters 1-9 of maths, I have 60 minutes")
    assert r.label == "Revision plan" and any("Chapter numbers" in u for u in r.uncertainty)


def test_long_sessions_get_a_break_and_never_exceed_time():
    ms = MasteryStore()
    chapters = [REGISTRY.chapter("sci.electricity"), REGISTRY.chapter("sci.light")]
    p = revision.plan(90, chapters=chapters, mastery=ms)
    assert p.total() <= 90 and any(b.activity == "break" for b in p.blocks)


def test_week_plan_is_daily_and_bounded():
    ms = MasteryStore()
    p = revision.week_plan(30, [REGISTRY.chapter("sci.electricity")], ms, exam_date=dt.date(2026, 10, 5),
                           today=dt.date(2026, 10, 1))
    assert len(p.days) == 4 and all(sum(b.minutes for b in bl) <= 30 for _, bl in p.days)


def test_spaced_repetition_intervals():
    now = time.time()
    s = TopicState(last_reviewed=now, streak=3, correct_independent=3)
    assert revision.next_review(s, now) == pytest.approx(now + 7 * 86400)
    s = TopicState(last_reviewed=now, streak=0, incorrect=3)
    assert revision.next_review(s, now) == pytest.approx(now + 86400)
    assert revision.next_review(None, now) == now


# ------------------------------------------------------------------------------ sessions
def test_session_commands_and_restoration(tmp_path):
    c = StudyCompanion(data_dir=tmp_path)
    assert "science" in c.ask("Start a science study session.").text()
    assert "Electricity" in c.ask("We're studying Electricity.").text()
    c.ask("Explain first, then quiz me")
    assert c.sessions.current.mode == "explain_then_quiz" and c.sessions.current.chapter == "sci.electricity"
    c.ask("Why is current opposite to electron flow?")
    c.ask("Check my answer", student_answer="d^2 = 800\nd = 800")
    c.ask("Pause studying")
    assert c.sessions.current.paused and c.sessions.current.keep_listening is False
    # A restart: a new companion over the same directory.
    c2 = StudyCompanion(data_dir=tmp_path)
    r = c2.ask("Continue from where we stopped")
    assert "Electricity" in r.text() and "current direction" in r.text()
    assert "Forgets the square root" in c2.ask("What did I get wrong?").text()
    r = c2.ask("End the session")
    assert "Session ended" in r.text() and c2.sessions.current is None
    assert c2.sessions.last.summary()["mistakes"] == 1


def test_session_never_keeps_the_microphone_open():
    sm = SessionManager()
    s = sm.start("physics", "sci.electricity")
    assert s.keep_listening is False
    assert "microphone" not in json.dumps(s.summary())


def test_unknown_chapter_is_admitted():
    c = StudyCompanion()
    r = c.ask("We're studying Quantum Field Theory")
    assert "Not in my installed syllabus" in r.text()
