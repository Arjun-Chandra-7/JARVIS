"""Facts with a window, and the vault that has to keep working without one.

The compatibility tests matter as much as the feature ones. Every line in the vault today has no
stamp, and this is worthless if adopting it means rewriting the file or if an unstamped line
stops being readable.
"""

from __future__ import annotations

from datetime import date

import pytest

from jarvis.memory import facts


def test_an_unstamped_line_is_true_and_always_has_been():
    """Every line in the vault today looks like this. If they stopped counting as facts, the
    whole profile would vanish the moment this module was consulted."""
    fact = facts.parse("- Location: Bangalore")
    assert fact is not None
    assert (fact.key, fact.value) == ("Location", "Bangalore")
    assert fact.holds_on(date(1999, 1, 1))
    assert fact.holds_on(date(2030, 1, 1))


def test_a_line_that_is_not_a_fact_is_left_alone():
    for line in ["## About", "", "Just a sentence.", "- no colon here"]:
        assert facts.parse(line) is None


def test_superseding_closes_the_old_window_and_opens_a_new_one():
    lines = ["- Machine: ThinkPad X1", "- Location: Bangalore"]
    out, changed = facts.supersede(lines, "Machine", "Bhramastra", on=date(2026, 3, 4))
    assert changed
    assert out[0] == "- Machine: ThinkPad X1 <!-- until:2026-03-04 -->"
    assert out[1] == "- Machine: Bhramastra <!-- since:2026-03-04 -->"
    assert out[2] == "- Location: Bangalore", "an unrelated fact was touched"


def test_the_old_value_is_still_answerable_afterwards():
    """The whole reason for closing rather than deleting. 'You told me in March' is the kind of
    thing an assistant is for, and it is unanswerable in a store that only holds the present."""
    lines, _ = facts.supersede(["- Machine: ThinkPad X1"], "Machine", "Bhramastra",
                               on=date(2026, 3, 4))
    now = [(f.key, f.value) for f in facts.current(lines, on=date(2026, 9, 20))]
    before = [(f.key, f.value) for f in facts.current(lines, on=date(2025, 6, 1))]
    assert now == [("Machine", "Bhramastra")]
    assert before == [("Machine", "ThinkPad X1")]


def test_restating_a_value_that_is_already_true_is_not_a_change():
    """Otherwise every session that re-reads the profile rewrites it, and the file grows a new
    line a day for facts nobody changed."""
    lines = ["- Machine: Bhramastra <!-- since:2026-03-04 -->"]
    out, changed = facts.supersede(lines, "Machine", "Bhramastra", on=date(2026, 9, 20))
    assert changed is False
    assert out == lines


def test_a_brand_new_key_is_appended_rather_than_replacing_anything():
    lines = ["- Machine: Bhramastra"]
    out, changed = facts.supersede(lines, "Phone", "Pixel", on=date(2026, 9, 20))
    assert changed is False, "adding a fact nobody held before is not a supersession"
    assert out[-1] == "- Phone: Pixel <!-- since:2026-09-20 -->"
    assert out[0] == "- Machine: Bhramastra"


def test_history_reads_oldest_first_with_both_windows():
    lines, _ = facts.supersede(["- Machine: ThinkPad X1"], "Machine", "Bhramastra",
                               on=date(2026, 3, 4))
    lines, _ = facts.supersede(lines, "Machine", "Framework", on=date(2026, 8, 1))
    told = [(f.value, f.since, f.until) for f in facts.history(lines, "Machine")]
    assert told == [
        ("ThinkPad X1", None, date(2026, 3, 4)),
        ("Bhramastra", date(2026, 3, 4), date(2026, 8, 1)),
        ("Framework", date(2026, 8, 1), None),
    ]


def test_only_the_open_line_is_current_after_two_changes():
    lines, _ = facts.supersede(["- Machine: A"], "Machine", "B", on=date(2026, 3, 4))
    lines, _ = facts.supersede(lines, "Machine", "C", on=date(2026, 8, 1))
    assert [f.value for f in facts.current(lines, on=date(2026, 9, 1))] == ["C"]
    assert [f.value for f in facts.current(lines, on=date(2026, 5, 1))] == ["B"]


def test_a_window_is_half_open_at_its_end():
    """The day a fact is replaced, the new value is the one that holds — otherwise both are
    true for a day and 'what is my machine' has two answers."""
    lines, _ = facts.supersede(["- Machine: A"], "Machine", "B", on=date(2026, 3, 4))
    assert [f.value for f in facts.current(lines, on=date(2026, 3, 4))] == ["B"]
    assert [f.value for f in facts.current(lines, on=date(2026, 3, 3))] == ["A"]


def test_the_key_is_matched_without_caring_about_case():
    lines, changed = facts.supersede(["- machine: A"], "Machine", "B", on=date(2026, 3, 4))
    assert changed
    assert "until:2026-03-04" in lines[0]


def test_a_malformed_stamp_does_not_take_the_line_with_it():
    """Hand-edited files will contain hand-edited mistakes."""
    fact = facts.parse("- Machine: A <!-- since:not-a-date -->")
    assert fact is not None
    assert fact.value == "A"
    assert fact.since is None


@pytest.mark.parametrize("indent", ["", "  ", "    "])
def test_indentation_is_preserved_when_a_fact_is_replaced(indent):
    lines, _ = facts.supersede([f"{indent}- Machine: A"], "Machine", "B", on=date(2026, 3, 4))
    assert lines[1].startswith(f"{indent}- Machine: B")
