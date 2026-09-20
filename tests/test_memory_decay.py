"""A recency prior, and the ways one turns into a deletion by accident.

The line this has to hold is that decay changes the *order* and never the *contents*. An old note
that is the only match must still be found; it just stops outranking a fresh one when both match.
"""

from __future__ import annotations

import time

import pytest

from jarvis.memory import decay

DAY = 86400.0
NOW = 1_800_000_000.0


def at(days_ago: float) -> float:
    return NOW - days_ago * DAY


def test_the_half_life_is_where_it_says_it_is():
    assert decay.weight(at(decay.HALF_LIFE_DAYS), now=NOW) == pytest.approx(0.5, abs=0.01)


def test_today_is_worth_full_weight():
    assert decay.weight(at(0), now=NOW) == pytest.approx(1.0)


def test_weight_only_ever_decreases_with_age():
    ages = [0, 10, 30, 90, 180, 365, 730]
    weights = [decay.weight(at(d), now=NOW) for d in ages]
    assert weights == sorted(weights, reverse=True)


def test_nothing_ever_decays_to_nothing():
    """A floor, not an asymptote: a genuinely old note still wins if nothing newer matches."""
    ancient = decay.weight(at(10_000), now=NOW)
    assert ancient == decay.MIN_WEIGHT
    assert ancient > 0


def test_an_unknown_date_is_not_punished():
    """A note whose age cannot be read should not be buried for it."""
    assert decay.weight(None, now=NOW) == 1.0
    assert decay.weight(0, now=NOW) == 1.0


def test_a_future_timestamp_does_not_score_above_one():
    """Clock skew and files touched by a sync should not invent a better-than-fresh note."""
    assert decay.weight(NOW + 30 * DAY, now=NOW) == 1.0


def test_importance_slows_the_fade_without_stopping_it():
    """An exemption would recreate the original problem for the notes most likely to be
    re-read, so importance flattens the curve rather than lifting anything out of it."""
    plain = decay.weight(at(730), now=NOW)
    important = decay.weight(at(730), now=NOW, importance=1.0)
    assert important > plain
    assert important < 1.0, "an important note stopped ageing entirely"


def test_importance_is_clamped_rather_than_trusted():
    """It arrives from stored metadata a human may have edited."""
    assert decay.weight(at(365), now=NOW, importance=99) == \
        decay.weight(at(365), now=NOW, importance=1.0)
    assert decay.weight(at(365), now=NOW, importance=-5) == \
        decay.weight(at(365), now=NOW, importance=0.0)


# --------------------------------------------------------------------------- re-ranking
def test_a_fresh_note_overtakes_a_slightly_better_old_one():
    """The case the whole module exists for."""
    scored = [("old", 0.90), ("fresh", 0.80)]
    ages = {"old": at(730), "fresh": at(3)}
    assert [k for k, _ in decay.apply(scored, ages, now=NOW)] == ["fresh", "old"]


def test_an_old_note_still_wins_when_it_is_the_only_match():
    """Decay reorders; it never removes. This is what separates it from a garbage collector."""
    scored = [("old", 0.90)]
    ages = {"old": at(3650)}
    ranked = decay.apply(scored, ages, now=NOW)
    assert [k for k, _ in ranked] == ["old"]
    assert ranked[0][1] > 0


def test_a_much_better_old_match_is_not_overturned_by_a_weak_fresh_one():
    """Recency is a tiebreaker, not the ranking. A strong old match should survive a barely
    relevant new one."""
    scored = [("old", 0.95), ("fresh", 0.05)]
    ages = {"old": at(60), "fresh": at(0)}
    assert decay.apply(scored, ages, now=NOW)[0][0] == "old"


def test_notes_with_no_recorded_age_keep_their_order():
    scored = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
    assert [k for k, _ in decay.apply(scored, {}, now=NOW)] == ["a", "b", "c"]


def test_the_real_clock_is_used_when_none_is_given():
    fresh = decay.weight(time.time())
    assert fresh == pytest.approx(1.0, abs=0.01)
