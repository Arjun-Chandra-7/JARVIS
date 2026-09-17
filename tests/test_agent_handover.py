"""Waiting for the answer, and changing agents before one runs dry."""
from __future__ import annotations

import time

from jarvis.coding import handover, roster, session, usage, watch


# ------------------------------------------------------------------ waiting for the answer
def _screen(text):
    return lambda: text


def test_nothing_is_announced_while_nothing_is_expected():
    watch.forget()
    assert watch.check(_screen("anything")) is None


def test_an_answer_is_not_announced_before_the_agent_has_started():
    watch.forget()
    watch.expect("Claude", "add a toggle")
    assert watch.check(_screen("thinking...")) is None
    watch.forget()


def test_a_screen_still_changing_is_not_finished():
    """All three agents redraw their prompt while still working, so a marker cannot be trusted —
    only the output going quiet."""
    watch.forget()
    watch.expect("Claude", "work")
    watch._waiting.asked_at -= 60
    assert watch.check(_screen("step one")) is None
    assert watch.check(_screen("step two")) is None
    watch.forget()


def test_a_settled_screen_is_announced_once_and_then_forgotten():
    watch.forget()
    watch.expect("Claude", "work")
    watch._waiting.asked_at -= 60
    screen = _screen("I refactored the parser into three functions and added tests for each.\n"
                     "Local: http://localhost:5173/")
    assert watch.check(screen) is None            # first sighting
    watch._waiting.last_change -= 60
    done = watch.check(screen)
    assert done is not None
    assert done.said.startswith("I refactored the parser")
    assert done.link == "http://localhost:5173/"
    assert watch.waiting_for() is None            # not announced twice
    assert watch.check(screen) is None


def test_an_agent_still_going_after_a_quarter_of_an_hour_is_given_up_on():
    watch.forget()
    watch.expect("Claude", "work")
    watch._waiting.asked_at = time.time() - watch.GIVE_UP_AFTER_S - 1
    assert watch.check(_screen("still going")) is None
    assert watch.waiting_for() is None


# ------------------------------------------------------------------ changing agents
def test_the_agent_that_is_finishing_cannot_take_over_from_itself(monkeypatch):
    monkeypatch.setattr("jarvis.coding.quota.usable",
                        lambda *a, **k: {"claude": True, "codex": True, "agy": True})
    following = handover.next_agent("claude", "add a feature")
    assert following is not None and following[0].name == "codex"


def test_nobody_free_means_the_current_agent_keeps_going(monkeypatch):
    """Nearly empty beats nothing."""
    monkeypatch.setattr("jarvis.coding.quota.usable",
                        lambda *a, **k: {"claude": True, "codex": False, "agy": False})
    assert handover.next_agent("claude", "add a feature") is None


def test_the_agent_is_asked_to_leave_rather_than_killed():
    """Typed, so the session closes cleanly and anything it wanted to write gets written."""
    assert handover.GOODBYE["claude"] == "/exit"


def test_the_summary_question_is_short_because_there_is_little_left_to_answer_with():
    assert len(handover.SUMMARY_REQUEST) < 300
    assert "next agent" in handover.SUMMARY_REQUEST


def test_five_percent_is_the_line():
    assert usage.HAND_OVER_AT == 5.0
    assert usage.Left(5.0).nearly_out() is True
    assert usage.Left(6.0).nearly_out() is False


def test_a_conversation_keeps_its_model_across_turns():
    session.end()
    live = session.begin(roster.BY_NAME["claude"], "opus", "medium", "/tmp")
    live.touch()
    assert (session.current().model, session.current().effort) == ("opus", "medium")
    session.end()
